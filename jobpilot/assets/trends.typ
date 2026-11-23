// JobPilot market trends report as a PDF:
//   typst compile --root <dir> --input data=<file>.json trends.typ out.pdf
// The JSON is built by `jp.trends.report.export_json` (`trends render`). Every value in it is a
// plain string or number and goes into the page as text (never through `eval`), so nothing a third
// party wrote can become Typst markup. The only links are the `url` of a source, which the script
// leaves empty unless it is a stored http(s) address.

#let data = json("/" + sys.inputs.at("data", default: "report.json"))

#let ink = rgb("#1d2433")
#let muted = rgb("#5b6475")
#let accent = rgb("#2f5bd3")
#let rule = rgb("#d9dde5")
#let soft = rgb("#eef1f6")
#let rising = rgb("#1f7a52")
#let falling = rgb("#b5452a")

#set document(title: data.title)
#set page(
  paper: "a4",
  margin: (x: 2cm, top: 2cm, bottom: 2.2cm),
  footer: context {
    set text(size: 8pt, fill: muted)
    data.title
    h(1fr)
    counter(page).display("1 / 1", both: true)
  },
)
// No font has to be installed: Libertinus Serif ships with Typst itself and is the last of the
// list. Inter or a common sans is used when the machine has one (Typst only warns about the others).
#set text(font: ("Inter", "Liberation Sans", "DejaVu Sans", "Libertinus Serif"), size: 10pt, fill: ink, lang: "en")
#set par(leading: 0.62em)
#show link: set text(fill: accent)
#show heading.where(level: 2): it => block(above: 17pt, below: 8pt, sticky: true, text(size: 12.5pt, weight: "bold", it.body))

// `[1][2]`, each a link to its source when it has an http(s) address.
#let urls = {
  let out = (:)
  for source in data.sources {
    out.insert(str(source.n), source.url)
  }
  out
}
#let cite-marks(cites) = {
  for n in cites {
    let mark = "[" + str(n) + "]"
    let url = urls.at(str(n), default: "")
    if url != "" { link(url, mark) } else { text(fill: muted, mark) }
  }
}

#let direction-color(direction) = if direction == "rising" { rising } else if direction == "falling" { falling } else { muted }

// Header.
#text(size: 8.5pt, fill: muted, tracking: 0.08em, upper("Market insight"))
#v(-4pt)
#text(size: 20pt, weight: "bold", data.title)
#v(-6pt)
#text(size: 9.5pt, fill: muted, data.period_title + "  ·  generated " + data.generated + "  ·  " + str(data.items_read) + " items read")
#v(-2pt)
#line(length: 100%, stroke: 0.6pt + rule)

// Sections of points.
#if data.sections.len() == 0 {
  text(fill: muted, "No point could be tied to a source this time.")
}
#for section in data.sections {
  heading(level: 2, section.title)
  for point in section.points {
    block(above: 8pt, below: 9pt, breakable: true, {
      if point.topic != "" {
        text(weight: "bold", point.topic + ": ")
      }
      point.text
      if point.cites.len() > 0 {
        h(3pt)
        text(size: 8.5pt, cite-marks(point.cites))
      }
      if point.quote != "" {
        block(
          above: 4pt,
          inset: (left: 8pt, y: 2pt),
          stroke: (left: 2pt + rule),
          text(size: 9pt, fill: muted, style: "italic", "“" + point.quote + "”"),
        )
      }
    })
  }
}

// Skill frequency, counted by the app.
#if data.skills.len() > 0 {
  heading(level: 2, "Skill frequency")
  text(size: 9pt, fill: muted, "Documents mentioning the skill, " + data.months + " (counted by the app, not by the model).")
  v(2pt)
  let top = calc.max(1, ..data.skills.map(s => s.total))
  let spark(counts) = if counts.len() == 0 { [] } else {
    let peak = calc.max(1, ..counts.map(c => c.count))
    box(height: 11pt, grid(
      columns: counts.len(),
      column-gutter: 1.5pt,
      align: bottom,
      ..counts.map(c => box(width: 4pt, height: 1pt + 10pt * c.count / peak, fill: accent.lighten(35%))),
    ))
  }
  table(
    columns: (auto, 1fr, auto, auto, auto),
    align: (left + horizon, left + horizon, right + horizon, left + horizon, left + horizon),
    stroke: none,
    inset: (x: 5pt, y: 3.5pt),
    fill: (_, y) => if y > 0 and calc.even(y) { soft } else { none },
    table.header(..("Skill", "", "Total", "By month", "Direction").map(h => text(size: 8.5pt, weight: "bold", fill: muted, h))),
    table.hline(stroke: 0.5pt + rule),
    ..data
      .skills
      .map(s => (
        text(weight: "medium", s.skill),
        box(width: 100% * (s.total / top), height: 7pt, fill: accent.lighten(55%)),
        str(s.total),
        spark(s.counts),
        text(fill: direction-color(s.direction), s.direction),
      ))
      .flatten(),
  )
}

// What the counts saw and did not see.
#if data.coverage.len() > 0 {
  block(above: 8pt, text(size: 8.5pt, fill: muted, data.coverage.join(" ")))
}

#if data.dropped > 0 {
  v(6pt)
  text(
    size: 8.5pt,
    fill: muted,
    style: "italic",
    str(data.dropped)
      + " point(s) were left out: no valid source, a quote that is not in the source, or a claim against the app's numbers.",
  )
}

// Numbered sources, by first use.
#if data.sources.len() > 0 {
  heading(level: 2, "Sources")
  for source in data.sources {
    block(below: 5pt, grid(
      columns: (1.6em, 1fr),
      column-gutter: 4pt,
      text(fill: muted, "[" + str(source.n) + "]"),
      {
        source.title
        if source.url != "" {
          linebreak()
          text(size: 8pt, link(source.url, source.url))
        }
      },
    ))
  }
}
