# JSON schemas

All data is JSON. Every key may be left out; text may be given as a number (`2024` reads as
`"2024"`); `null` reads as empty; unknown keys are ignored.

## Profile

What you draft from the documents and pass to `profile check --file`. Plain text only, no markup.

| Key | Value |
| --- | --- |
| `basics` | object: `name`, `headline`, `email`, `phone`, `location`, `github`, `linkedin`, `website` (all text) |
| `summary` | list of text |
| `skills` | list of `{category, items}`; `items` is a list of text, one skill each |
| `experience` | list of `{company, position, location, url, start, end, bullets, groups}`; `bullets` is a list of text; `groups` is a list of `{title, bullets}` |
| `projects` | list of `{name, role, url, start, end, intro, bullets}` |
| `education` | list of `{institution, degree, location, gpa, start, end, bullets}` |
| `certifications` | list of `{name, issuer, url, date}` |
| `languages` | list of `{language, fluency}` |

```json
{
  "basics": {"name": "Ann Lee", "headline": "Backend engineer", "email": "ann@lee.dev",
             "phone": "+84 912 345 678", "location": "Hanoi", "github": "github.com/annlee",
             "linkedin": "", "website": ""},
  "summary": ["Backend engineer in Hanoi working in Rust and C#."],
  "skills": [{"category": "Languages", "items": ["Rust", "C#"]}],
  "experience": [
    {"company": "Acme Corp", "position": "Senior Engineer", "location": "Hanoi", "url": "",
     "start": "Jan 2021", "end": "Present",
     "bullets": ["Cut API latency by 40% for 3 teams by moving hot paths to Rust."], "groups": []}
  ],
  "projects": [{"name": "Ferry", "role": "maintainer", "url": "github.com/annlee/ferry",
                "start": "2022", "end": "", "intro": "An open-source job queue for Rust.", "bullets": []}],
  "education": [{"institution": "Hanoi University of Science", "degree": "BSc Computer Science",
                 "location": "", "gpa": "", "start": "2015", "end": "2019", "bullets": []}],
  "certifications": [],
  "languages": [{"language": "English", "fluency": "fluent"}]
}
```

The stored file `profile.json` wraps it: `{"cv": {...}, "left_out": [...], "built_at": "...",
"documents_hash": "..."}` (empty values are left out of `cv`). Read it with `profile show`; never
write it by hand.

## CV

What you write for one job and pass to `cv check --file`. The profile's keys, plus layout:

| Key | Value |
| --- | --- |
| `sections` | list: the order of the sections, any of `summary`, `skills`, `experience`, `projects`, `education`, `certifications`, `languages`. A section not listed is not shown. Left out = all, in that order. |
| `titles` | object: section key -> its heading, e.g. `{"projects": "Open Source"}` |
| `style` | object: `accent-color` (`#0092b8`), `font` (`New Computer Modern`), `font-size` (`10pt`), `paper` (`a4` or `us-letter`). Leave it out unless the user asks. |
| `hidden` | on any item of `skills`, `experience`, `projects`, `education`, `certifications`, `languages`: `true` keeps it in the file but does not show it |

Markup: in `summary` lines, every `bullets` item, a group `title` and a project `intro` you may
use `**bold**`, `_italic_` and `[text](https://url)`. Everything else, and every other field, is
shown exactly as written.

What `cv check` requires (the fact guard):

- `basics.name` equals the profile's; `email`, `phone`, `github`, `linkedin`, `website` are empty
  or equal; `location` is a location of the profile.
- every `experience` item is a profile role: same `company`, `position`, `start` and `end`.
- every project, school, certification and language is in the profile, with the same role, dates,
  degree, GPA, issuer and fluency where given.
- every number a reader sees appears in the profile; every link address appears in the profile;
  every skill item appears in the profile (case aside; K8s/Kubernetes, JS/JavaScript,
  TS/TypeScript, Postgres/PostgreSQL, Go/Golang, Mongo/MongoDB, Node/Node.js count as the same).

```json
{
  "basics": {"name": "Ann Lee", "headline": "Senior Backend Engineer", "email": "ann@lee.dev",
             "phone": "+84 912 345 678", "location": "Hanoi", "github": "github.com/annlee"},
  "sections": ["summary", "skills", "experience", "projects", "education", "languages"],
  "summary": ["Backend engineer building **Rust** services on Kubernetes and PostgreSQL."],
  "skills": [{"category": "Languages", "items": ["Rust", "C#"]}],
  "experience": [
    {"company": "Acme Corp", "position": "Senior Engineer", "location": "Hanoi",
     "start": "Jan 2021", "end": "Present",
     "bullets": ["Cut API latency by **40%** for 3 teams by moving hot paths to Rust."]}
  ],
  "projects": [{"name": "Ferry", "role": "maintainer", "url": "github.com/annlee/ferry", "start": "2022",
                "intro": "Open-source job queue for Rust ([source](https://github.com/annlee/ferry))."}],
  "education": [{"institution": "Hanoi University of Science", "degree": "BSc Computer Science",
                 "start": "2015", "end": "2019"}],
  "languages": [{"language": "English", "fluency": "fluent"}]
}
```

A full example: `assets/example-cv.json`.

## Analysis

What you extract from a posting and pass to `job add --file`. `role` is required.

| Key | Value |
| --- | --- |
| `company` | the hiring company |
| `role` | the job title as written |
| `slug` | kebab-case `company-role`, ASCII, at most 50 characters |
| `location` | city/country or `remote`; `""` if unknown |
| `apply_url` | an absolute `http(s)` link from the posting, else `""` (anything else is dropped) |
| `must_have`, `nice_to_have`, `key_terms` | lists of short text |
| `seniority` | `intern`, `junior`, `mid`, `senior`, `staff`, `lead`, `manager` or `""` |

```json
{
  "company": "Northwind", "role": "Senior Backend Engineer", "slug": "northwind-senior-backend-engineer",
  "location": "Remote", "apply_url": "https://northwind.example/jobs/42/apply",
  "must_have": ["Rust", "PostgreSQL", "Kubernetes"], "nice_to_have": ["Kafka"],
  "key_terms": ["Rust", "Kubernetes", "PostgreSQL", "logistics"], "seniority": "senior"
}
```

## Report

What you pass to `cv check --report`. Three lists of short text.

| Key | Value |
| --- | --- |
| `emphasised` | what the CV leads with and why |
| `omitted` | what was dropped from the profile |
| `gaps` | requirements of the posting the profile has no evidence for |

```json
{
  "emphasised": ["Rust services at Acme Corp: the posting's main requirement"],
  "omitted": ["Initech role: internal billing tools, not relevant"],
  "gaps": ["Kafka"]
}
```
