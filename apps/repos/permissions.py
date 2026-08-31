"""Permission constants for repository operator views."""

REPOSITORY_WORK_VIEW_PERMISSIONS = (
    "repos.view_githubrepository",
    "repos.view_repositoryissue",
    "repos.view_repositorypullrequest",
)
REPOSITORY_WORK_SYNC_PERMISSIONS = (
    "repos.add_repositoryissue",
    "repos.change_repositoryissue",
    "repos.add_repositorypullrequest",
    "repos.change_repositorypullrequest",
)
REPOSITORY_WORK_LABEL_PERMISSIONS = (
    "repos.change_repositoryissue",
    "repos.change_repositorypullrequest",
)
REPOSITORY_WORK_ASSIGNMENT_ADD_PERMISSIONS = (
    "repos.add_repositoryworkassignment",
)
REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS = (
    "repos.change_repositoryworkassignment",
)
REPOSITORY_WORK_ASSIGNMENT_PERMISSIONS = (
    *REPOSITORY_WORK_ASSIGNMENT_ADD_PERMISSIONS,
    *REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS,
)
