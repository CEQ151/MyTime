# Single source of truth for the app version. The release workflow rewrites
# APP_VERSION from the pushed tag before building, so a tagged build always
# reports the tag's version.

APP_VERSION = "3.2.1"

GITHUB_OWNER = "CEQ151"
GITHUB_REPO = "MyTime"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
# Public changelog page (rendered live from the repo's CHANGELOG.md); opened in the browser
# after a successful in-app update.
CHANGELOG_PAGE = "https://caienqi.xyz/changelog.html"
