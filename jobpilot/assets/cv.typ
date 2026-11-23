// Renders a CV from a JSON data file. Self-contained: no package, no network.
// Usage: typst compile --root <dir> --input data=<file>.json cv.typ out.pdf
// The data is written by `scripts/jp/render.py` (`typst_data`): the markup fields (summary lines,
// bullets, group titles, project intros) are already Typst markup with everything else escaped.

#let data = json("/" + sys.inputs.at("data", default: "cv.json"))
#let b = data.at("basics", default: (:))
#let style = data.at("style", default: (:))

#let md(s) = eval(str(s), mode: "markup")
#let field(item, key) = {
  let value = item.at(key, default: "")
  if value == none { "" } else { str(value).trim() }
}
#let items-of(key) = {
  let value = data.at(key, default: ())
  if value == none { () } else { value }
}
#let visible(items) = items.filter(i => type(i) != dictionary or not i.at("hidden", default: false))
#let bullets(xs) = if xs != none and xs.len() > 0 { list(..xs.map(md)) }

#let default-titles = (
  summary: "Summary",
  skills: "Skills",
  experience: "Experience",
  projects: "Projects",
  education: "Education",
  certifications: "Certifications",
  languages: "Languages",
)
#let titles = default-titles + data.at("titles", default: (:))

// `rgb` refuses anything but a colour, so the hex below is safe to put in SVG markup.
#let accent-color = rgb(style.at("accent-color", default: "#0092b8"))
#let accent = accent-color.to-hex()
#let font-size = float(str(style.at("font-size", default: "10pt")).trim("pt", at: end)) * 1pt

// A profile link as `host/path`: any scheme, `www.` and trailing `/` dropped; a bare handle
// (`annlee`, `@annlee`) gets the site's prefix (`github.com/`).
#let bare-url(s, handle-prefix: "") = {
  let u = str(s).trim()
  for p in ("https://", "http://", "www.") {
    if lower(u).starts-with(p) { u = u.slice(p.len()) }
  }
  u = u.trim("/", at: end)
  if handle-prefix != "" and u != "" and not u.contains("/") and not u.contains(".") {
    u = handle-prefix + u.trim("@", at: start)
  }
  u
}

// Brand marks (Simple Icons, CC0), drawn in the accent colour.
#let icon-paths = (
  github: "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12",
  linkedin: "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 4.9v4.837zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z",
)
#let icon(name) = box(baseline: 0.12em, image(
  bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='" + accent + "' d='" + icon-paths.at(name) + "'/></svg>"),
  format: "svg",
  height: 0.9em,
  alt: name,
))

#let site-link(url, body) = if url == "" { body } else { link("https://" + url, body) }
#let web-link(key, handle-prefix: "", icon-name: none) = {
  let u = bare-url(field(b, key), handle-prefix: handle-prefix)
  if u != "" {
    // a word joiner keeps the icon on the line of the text; a long URL can still wrap
    link("https://" + u)[#if icon-name != none [#icon(icon-name)#h(0.25em)#sym.wj]#u]
  }
}

// ---- page and text ----

#set document(author: field(b, "name"), title: field(b, "name"))
#set text(
  font: style.at("font", default: "New Computer Modern"),
  size: font-size,
  lang: "en",
  // no ligatures, so an applicant-tracking system reads the text as written
  ligatures: false,
)
#set page(margin: 0.5in, paper: style.at("paper", default: "a4"))
#set par(leading: 0.6em, spacing: 0.9em)
#set list(spacing: 0.6em)

#show link: underline
#show link: set text(fill: accent-color)
#show heading: set text(fill: accent-color)
// Section title: small caps over a rule.
#show heading.where(level: 2): it => block(above: 1.05em, below: 0.7em, sticky: true, stack(
  spacing: 0.4em,
  smallcaps(it.body),
  line(length: 100%, stroke: 1pt),
))

// ---- header ----

#block(below: 0.9em, text(weight: 700, size: 20pt, fill: accent-color, field(b, "name")))
#{
  let phone = field(b, "phone")
  let email = field(b, "email")
  let parts = (
    field(b, "headline"),
    if phone != "" { link("tel:" + phone.replace(" ", ""), phone) },
    field(b, "location"),
    if email != "" { link("mailto:" + email, email) },
    web-link("github", handle-prefix: "github.com/", icon-name: "github"),
    web-link("linkedin", handle-prefix: "linkedin.com/in/", icon-name: "linkedin"),
    web-link("website"),
  ).filter(x => x != none and x != "")
  if parts.len() > 0 { block(parts.join("  |  ")) }
}

#set par(justify: true)

// ---- entries ----

#let dates(i) = {
  let (start, end) = (field(i, "start"), field(i, "end"))
  if start == "" { end } else if end == "" { start } else { start + " " + sym.dash.em + " " + end }
}
// One line: left, and right pushed to the margin.
#let one-by-two(left, right) = block(above: 1.15em, below: 0.75em)[#left #h(1fr) #right]
// Two lines of left / right.
#let two-by-two(top-left, top-right, bottom-left, bottom-right) = block(above: 1.15em, below: 0.75em)[
  #top-left #h(1fr) #top-right \
  #bottom-left #h(1fr) #bottom-right
]
#let in-parens(url) = if url != "" [ (#link("https://" + url, url))]

#let render-summary() = bullets(items-of("summary"))

#let render-skills() = list(..visible(items-of("skills")).map(s => [*#field(s, "category")*: #s.at("items", default: ()).join(", ")]))

#let render-experience() = {
  for e in visible(items-of("experience")) {
    two-by-two(
      strong(field(e, "position")),
      dates(e),
      site-link(bare-url(field(e, "url")), field(e, "company")),
      emph(field(e, "location")),
    )
    bullets(e.at("bullets", default: ()))
    for g in e.at("groups", default: ()) {
      block(emph(md(g.at("title", default: ""))))
      bullets(g.at("bullets", default: ()))
    }
  }
}

#let render-projects() = {
  for p in visible(items-of("projects")) {
    let url = bare-url(field(p, "url"))
    let (role, name, when) = (field(p, "role"), field(p, "name"), dates(p))
    one-by-two(
      {
        if role == "" { strong(name) } else [*#role*, #name]
        if when != "" { in-parens(url) }
      },
      if when == "" and url != "" { link("https://" + url, url) } else { when },
    )
    let intro = field(p, "intro")
    if intro != "" { block(md(intro)) }
    bullets(p.at("bullets", default: ()))
  }
}

#let render-education() = {
  for e in visible(items-of("education")) {
    let gpa = field(e, "gpa")
    two-by-two(
      strong(field(e, "institution")),
      dates(e),
      emph(field(e, "degree") + if gpa != "" { " (GPA: " + gpa + ")" }),
      emph(field(e, "location")),
    )
    bullets(e.at("bullets", default: ()))
  }
}

#let render-certifications() = {
  for c in visible(items-of("certifications")) {
    let issuer = field(c, "issuer")
    one-by-two(
      [*#field(c, "name")*#if issuer != "" [, #issuer]#in-parens(bare-url(field(c, "url")))],
      field(c, "date"),
    )
  }
}

#let render-languages() = list(..visible(items-of("languages")).map(l => [*#field(l, "language")*: #field(l, "fluency")]))

#let renderers = (
  summary: render-summary,
  skills: render-skills,
  experience: render-experience,
  projects: render-projects,
  education: render-education,
  certifications: render-certifications,
  languages: render-languages,
)

#{
  let order = data.at("sections", default: none)
  if order == none or order.len() == 0 { order = renderers.keys() }
  for key in order {
    if key in renderers and visible(items-of(key)).len() > 0 {
      heading(level: 2, titles.at(key))
      renderers.at(key)()
    }
  }
}
