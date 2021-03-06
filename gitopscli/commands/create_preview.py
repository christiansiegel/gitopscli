import hashlib
import logging
import os
import shutil
import uuid

from gitopscli.git.create_git import create_git
from gitopscli.yaml.gitops_config import GitOpsConfig
from gitopscli.yaml.yaml_util import update_yaml_file
from gitopscli.gitops_exception import GitOpsException

# pylint: disable=too-many-statements


def create_preview_command(
    command,
    pr_id,
    parent_id,
    username,
    password,
    git_user,
    git_email,
    create_pr,
    auto_merge,
    organisation,
    repository_name,
    git_provider,
    git_provider_url,
):
    assert command == "create-preview"

    apps_tmp_dir = __create_tmp_dir()
    root_tmp_dir = __create_tmp_dir()

    try:
        apps_git = create_git(
            username,
            password,
            git_user,
            git_email,
            organisation,
            repository_name,
            git_provider,
            git_provider_url,
            apps_tmp_dir,
        )

        pr_branch = apps_git.get_pull_request_branch(pr_id)

        apps_git.checkout(pr_branch)
        logging.info("App repo PR branch %s checkout successful", pr_branch)
        shortened_branch_hash = hashlib.sha256(pr_branch.encode("utf-8")).hexdigest()[:8]
        logging.info("Hashed branch %s to hash: %s", pr_branch, shortened_branch_hash)
        try:
            gitops_config = GitOpsConfig(apps_git.get_full_file_path(".gitops.config.yaml"))
        except FileNotFoundError as ex:
            raise GitOpsException(f"Couldn't find .gitops.config.yaml") from ex
        logging.info("Read .gitops.config.yaml: %s", gitops_config)

        root_git = create_git(
            username,
            password,
            git_user,
            git_email,
            gitops_config.team_config_org,
            gitops_config.team_config_repo,
            git_provider,
            git_provider_url,
            root_tmp_dir,
        )
        root_git.checkout("master")
        logging.info("Config repo branch master checkout successful")

        config_branch = f"gitopscli-create-preview-{str(uuid.uuid4())[:8]}" if create_pr else "master"
        if create_pr:
            root_git.new_branch(config_branch)
            logging.info("Created branch %s in config repo", config_branch)

        preview_template_folder_name = ".preview-templates/" + gitops_config.application_name
        if os.path.isdir(root_git.get_full_file_path(preview_template_folder_name)):
            logging.info("Using the preview template folder: %s", preview_template_folder_name)
        else:
            raise GitOpsException(f"The preview template folder does not exist: {preview_template_folder_name}")

        new_preview_folder_name = gitops_config.application_name + "-" + shortened_branch_hash + "-preview"
        logging.info("New folder for preview: %s", new_preview_folder_name)
        branch_preview_env_already_exist = os.path.isdir(root_git.get_full_file_path(new_preview_folder_name))
        logging.info("Is preview env already existing for branch? %s", branch_preview_env_already_exist)
        if not branch_preview_env_already_exist:
            __create_new_preview_env(
                pr_branch,
                new_preview_folder_name,
                preview_template_folder_name,
                root_git,
                gitops_config.application_name,
            )
        new_image_tag = apps_git.get_last_commit_hash()
        logging.info("Using image tag from last app repo commit: %s", new_image_tag)
        route_host = None
        value_replaced = False
        for replacement in gitops_config.replacements:
            route_host, value_replaced = __replace_value(
                gitops_config,
                new_image_tag,
                new_preview_folder_name,
                replacement,
                root_git,
                route_host,
                shortened_branch_hash,
                value_replaced,
            )
        if not value_replaced:
            __no_deployment_needed(apps_git, new_image_tag, parent_id, pr_id)
            return
        root_git.commit(f"Update preview environment for '{gitops_config.application_name}' and branch '{pr_branch}'.")
        root_git.push(config_branch)
        logging.info("Pushed branch %s", config_branch)
        pr_comment_text = f"""
New preview environment for `{gitops_config.application_name}` and branch `{pr_branch}` created successfully. Access it here:
https://{route_host}
"""
        if branch_preview_env_already_exist:
            pr_comment_text = f"""
Preview environment for `{gitops_config.application_name}` and branch `{pr_branch}` updated successfully. Access it here:
https://{route_host}
"""
        logging.info("Creating PullRequest comment for pr with id %s and content: %s", pr_id, pr_comment_text)
        apps_git.add_pull_request_comment(pr_id, pr_comment_text, parent_id)
    finally:
        shutil.rmtree(apps_tmp_dir, ignore_errors=True)
        shutil.rmtree(root_tmp_dir, ignore_errors=True)
    if create_pr:
        pull_request = __create_pullrequest(config_branch, gitops_config, root_git)
        if auto_merge:
            __merge_pullrequest(config_branch, pull_request, root_git)


def __replace_value(
    gitops_config,
    new_image_tag,
    new_preview_folder_name,
    replacement,
    root_git,
    route_host,
    shortened_branch_hash,
    value_replaced,
):
    replacement_value = None
    logging.info("Replacement: %s", replacement)
    replacement_path = replacement["path"]
    replacement_variable = replacement["variable"]
    if replacement_variable == "GIT_COMMIT":
        replacement_value = new_image_tag
    elif replacement_variable == "ROUTE_HOST":
        route_host = gitops_config.route_host.replace("{SHA256_8CHAR_BRANCH_HASH}", shortened_branch_hash)
        logging.info("Created route host: %s", route_host)
        replacement_value = route_host
    else:
        logging.info("Unknown replacement variable: %s", replacement_variable)
    try:
        value_replaced = value_replaced | update_yaml_file(
            root_git.get_full_file_path(new_preview_folder_name + "/values.yaml"), replacement_path, replacement_value,
        )
    except KeyError as ex:
        raise GitOpsException(f"Key '{replacement_path}' not found in '{new_preview_folder_name}/values.yaml'") from ex
    logging.info("Replacing property %s with value: %s", replacement_path, replacement_value)
    return route_host, value_replaced


def __no_deployment_needed(apps_git, new_image_tag, parent_id, pr_id):
    logging.info("The image tag %s has already been deployed. Doing nothing.", new_image_tag)
    pr_comment_text = f"""
The version `{new_image_tag}` has already been deployed. Nothing to do here.
"""
    logging.info("Creating PullRequest comment for pr with id %s and content: %s", pr_id, pr_comment_text)
    apps_git.add_pull_request_comment(pr_id, pr_comment_text, parent_id)


def __create_new_preview_env(
    pr_branch, new_preview_folder_name, preview_template_folder_name, root_git, app_name,
):
    shutil.copytree(
        root_git.get_full_file_path(preview_template_folder_name), root_git.get_full_file_path(new_preview_folder_name),
    )
    chart_file_path = new_preview_folder_name + "/Chart.yaml"
    logging.info("Looking for Chart.yaml at: %s", chart_file_path)
    if root_git.get_full_file_path(chart_file_path):
        try:
            update_yaml_file(root_git.get_full_file_path(chart_file_path), "name", new_preview_folder_name)
        except KeyError as ex:
            raise GitOpsException(f"Key 'name' not found in '{chart_file_path}'") from ex
    root_git.commit(f"Create new preview environment for '{app_name}' and branch '{pr_branch}'.")


def __create_pullrequest(branch, gitops_config, root_git):
    title = "Updated preview environment for " + gitops_config.application_name
    description = f"""
This Pull Request is automatically created through [gitopscli](https://github.com/baloise/gitopscli).
"""
    pull_request = root_git.create_pull_request(branch, "master", title, description)
    logging.info("Pull request created: %s", {root_git.get_pull_request_url(pull_request)})
    return pull_request


def __merge_pullrequest(branch, pull_request, root_git):
    root_git.merge_pull_request(pull_request)
    logging.info("Pull request merged")
    root_git.delete_branch(branch)
    logging.info("Branch '%s' deleted", branch)


def __create_tmp_dir():
    tmp_dir = f"/tmp/gitopscli/{uuid.uuid4()}"
    os.makedirs(tmp_dir)
    logging.info("Created directory %s", tmp_dir)
    return tmp_dir
