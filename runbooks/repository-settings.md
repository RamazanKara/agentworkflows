# GitHub repository settings

Repository-host controls are declared in `.github/repository-settings.json`. Audit them with an
authenticated GitHub CLI session:

```bash
python3 scripts/github-settings.py
```

The command is read-only by default and exits non-zero on drift. A repository owner can apply the
declared settings explicitly:

```bash
python3 scripts/github-settings.py --apply
```

The apply mode enables private vulnerability reporting, vulnerability alerts, automated security
updates, secret scanning and push protection; enables Discussions; normalizes merge behavior; and
protects `main` with required CI checks and review/conversation rules, publishes project topics and
the docs homepage, and creates a reviewer-gated `pypi` environment restricted to `v*` tags. If a GitHub plan
or organization policy rejects a feature, keep the declaration as is, record the result, and
resolve it at the account level.

Run the audit after renaming CI jobs because required-check context names are exact. Before running
`--apply` against a fork or mirror, review the resolved repository printed by `gh repo view`.

## Why `enforce_admins` is false

The declaration sets `enforce_admins: false` deliberately. GitHub requires an approving reviewer
other than the author, so `required_approving_review_count: 1` needs a second maintainer. With
admin enforcement off, a sole maintainer cuts releases with every branch-protection control kept
in place.

For everyone, maintainer included: all ten required status checks must pass, conversations must
be resolved, force pushes and deletions stay blocked, and a contributor's pull request needs a
review. The maintainer can merge their own reviewed work.

Once the project has more than one maintainer with write access, the review requirement becomes
satisfiable; set `enforce_admins` back to true at that point.
