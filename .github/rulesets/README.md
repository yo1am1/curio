# Repository rulesets

GitHub rulesets cannot be applied by a workflow token, so they live here as code and are
imported once by a repository admin: **Settings → Rules → Rulesets → New ruleset → Import a
ruleset**, then pick each file.

| File | Applies to | Enforces |
|---|---|---|
| [`main.json`](main.json) | the default branch | changes only through pull requests, squash merges only, the `checks` (CI) and `pr-title` checks green on an up-to-date branch, resolved review threads, linear history, no force pushes, no deletion |
| [`release-tags.json`](release-tags.json) | tags `v*` | release tags cannot be moved or deleted (the Release workflow can still create them) |

The required approving reviews are 0 because a sole maintainer cannot approve their own pull
request; raise it when there are more maintainers. Change a ruleset here first, in a pull
request, then re-import it, so the file stays the source of truth.

Repository settings that go with them (**Settings → General**):

- Pull requests: allow **squash merging** only, with "Default to pull request title";
  tick **Automatically delete head branches**.
- **Environments → release**: created by the first release run; add a deployment branch rule
  for `main` only.
