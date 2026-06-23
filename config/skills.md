# ScholarReach writing playbook

This file is the single source of truth for how Raj's outreach and application
artefacts read. The email and document generators stitch these sections into
their prompts — edit a section here and every subsequent run follows.

---

## 0. Shared rules (all artefacts)

**Voice.** Professional, direct, specific. No flattery, no buzzword strings,
no hedging. Treat the reader (a professor or admissions committee) as a peer
who has read a thousand applications and respects evidence over polish.

**Grounding rule.** Every claim about Raj — experience, skill, dataset, result,
method, project name, GPA, transcript — must be traceable to `profile.yaml`.
If a fact is not in the profile, do NOT assert it. When in doubt, leave it out
or write it as a hypothesis ("I would like to explore…") rather than a
finished claim.

**Citation rule.** Reference the professor's work ONLY via the exact paper
titles supplied in the prompt. Do not paraphrase paper titles. Do not cite
papers you were not given. The cited paper must be referenced by its full
exact title, in quotes.

**Forbidden phrases.** See `config.yaml::quality_gate.banned_phrases` for the
canonical banned list (the quality gate rejects drafts that contain them).
Never use: "I am writing to express", "esteemed", "I came across", "I hope
this email finds you well", "to whom it may concern", "I am interested in",
"I am fascinated by", "I am drawn to", "I am passionate about", "I have
always been passionate". These are repetitive openers; rephrase.

**Honesty (non-negotiable).** Never claim a project, skill, or result that is
not true. Do not reframe one domain as another (e.g. "if your contrastive
learning work was on visual place recognition, don't reframe it as medical
imaging"). When background doesn't match, say so in one honest sentence then
pivot to genuine strengths — don't over-apologize or under-disclose.

**Subject lines.** Maximum 9 words. No emoji. No all-caps. State the topic or
position, not the genre ("PhD application" is fine; "Application for…" is not).
If the posting specifies an exact subject line or code (e.g.
`[PhD Multimodal AI]`, `AlpiApplication2026`), use it **exactly** — this is
often a screening filter.

**Output.** Return only the document text. No preamble, no "Here is your
draft:", no trailing signature block beyond what the structure requires.

### Voice (applications) — SOP, cover letter, motivation letter, research proposal

These rules apply to the four competitive application artefacts. They are
**in addition to** the shared rules above, not a replacement.

- **British English.** "organise", "modelling", "behaviour", "colour",
  "centre", "specialise", "summarise", "analyse", "optimisation", "travelling".
  No American spelling in those words.
- **No AI-generated phrasing.** Avoid stock phrases: "I am excited to apply",
  "I would be a great fit", "thrilled at the opportunity", "I believe I am a
  strong candidate", "I look forward to hearing from you". If a sentence
  could appear in a hundred other applications, rewrite it.
- **No repetitive sentence starts.** Don't open multiple sentences with "I".
  Vary the subject: the work, the gap, the method, the paper, the question,
  the dataset, the lab. The reader should feel Raj is talking *about* the
  research, not about himself.
- **Vary sentence length naturally.** Mix short declarative sentences with
  longer compound ones. The point is rhythm, not padding.
- **Human, academic, direct.** No exclamation marks. No emoji. No "I" in
  every line. No marketing-speak. Treat the reader as a researcher, not a
  recruiter.
- **No exaggeration.** Do not claim expertise Raj does not have. Do not
  inflate results. Do not claim papers in submission as published. Do not
  claim "fluent" in a language when "conversational" is honest.
- **Honest fit.** Where the background does not perfectly match, say so in
  one sentence and pivot to genuine strengths. Never over-apologise, never
  under-disclose.
- **Show curiosity, not credentials.** A research artefact reads as a future
  researcher's voice when it asks questions, names gaps, and proposes
  directions. It reads as a job applicant when it lists skills.
- **Gap + direction, not gap + solution.** When identifying a research gap,
  describe a *possible* direction. Do not claim the gap is closed, the
  method is best, or the result is novel — those claims belong in a paper,
  not in an application.
- **Long-term academic goals.** Where relevant (PhD and predoctoral
  applications especially), anchor the close in the long-term direction:
  the research career Raj is building towards, not the immediate next
  position.
- **European tone.** For European applications (ETH, EPFL, ETH AI Center,
  UZH, Simula, University of Oslo, and similar), lean research-oriented
  and intellectually mature. Less "fit narrative", more "research
  questions I want to spend four years on".

### Position types this playbook covers

The Cover / Motivation letter and Statement of Purpose sections below
apply to all of these position types — the model should adapt the tone and
depth, not the structure:

- **PhD positions** — research questions, intellectual curiosity, fit with
  the lab, long-term academic goals. Most depth.
- **Predoctoral positions** — research preparation, methods, gap-identification.
  Treat the role as a stepping-stone to a PhD.
- **Research internships** — concrete methods, a short project idea, what
  Raj would deliver in the time available. Less long-term framing.
- **Research assistant roles** — engineering rigour, evaluation discipline,
  specific deliverables. Closer to "applied researcher" than "future
  professor".

---

## 1. Research-fit workflow (run before drafting any application artefact)

Before writing a single line of the artefact, walk through these five
steps. They are not part of the output — they are the thinking that
produces a good draft.

1. **Analyse the research fit.** One or two lines: what does the lab or
   position work on, and where does it sit relative to Raj's research
   experience (in `profile.yaml`)? Be specific: name the lab's theme, not
   the field at large.
2. **Identify the strongest alignment.** Pick **one** named project from
   `profile.yaml` (VinDr-Mammo / VPR / SAE superposition / RAG) whose
   method or domain maps most directly onto the lab's direction. The
   other projects may be mentioned in passing; the foregrounded one
   carries the draft.
3. **Identify a realistic research gap or open question.** Grounded in
   one cited paper from the prompt. The gap should be a *plausible* next
   question that builds on the paper — not a critique, not a solved
   problem repackaged. Name the gap, name the method Raj would use to
   approach it, and stop. Do not claim the gap is closed.
4. **Draft the artefact** following the per-section structure in
   `## 2. Cover / Motivation letter`, `## 3. Statement of Purpose`, or
   `## 4. Research Proposal`. Each section's word-count target is a
   target, not a ceiling.
5. **Self-check before returning the draft.** Read it once. Does it read
   like a *future researcher* applying to do research — asking questions,
   naming gaps, proposing directions? Or does it read like a *job
   applicant* listing skills and past roles? If the latter, revise: move
   the verbs off Raj and onto the research. A researcher's draft is
   mostly about the work; the candidate is the one proposing to do it.

---

## 2. Email (advertised + speculative)

### Before you write

- Read the actual posting twice. Note exact requirements, the application
  channel (email vs. portal vs. form), any required subject line format, and
  the deadline.
- If the posting says "no applications via email" or "applications only
  through the portal", do NOT send — surface that to the user instead.
- Decide fit category:
  - **Strong fit** — background maps directly onto 2+ requirements. Lead
    with the technical match.
  - **Genuine stretch** — lacks core requirements but has honest adjacent
    skills. Lead with field enthusiasm, then be transparent about the gap.
  - **Significant stretch** — most requirements are missing. Still worth a
    short, honest email if the field excites, but keep expectations low and
    never overstate.

### Structure (6 paragraphs, 220–320 words)

1. **Hook (~30 words).** Position title + lab + the most relevant verified
   paper cited by exact title + one clause of context for *why* the paper
   matters (a method or finding, not "I found it interesting"). Mention the
   professor's name.
2. **Gap (~50 words).** What the paper does not yet address, grounded in the
   identified gap field. Do not invent gaps.
3. **Angle (~60 words).** What Raj could explore, tied to the foregrounded
   project's method (ViT, contrastive learning, RAG, SAE features, etc.).
   Name the method.
4. **Evidence (~50 words).** One concrete, traceable result or dataset from
   the foregrounded project (e.g. "VinDr-Mammo explainability work"), not a
   generic adjective.
5. **Ask (~40 words).** Explicit ask to be considered for the position + a
   15-minute call in the professor's timezone window (Mon–Thu, 08:00–09:00
   local). If the formal application goes through a portal, say so
   explicitly: "I understand formal applications go through [portal] and I
   am preparing a complete submission. I wanted to introduce myself
   beforehand."
6. **Attachments + sign-off (~15 words).** One attachment line + name +
   email + GitHub + LinkedIn (optional).

### Subject line
- If the posting specifies an exact subject line or code, use it **exactly**.
- Otherwise: `PhD Enquiry — [Research Area]` (advertised) or
  `Prospective PhD applicant — [topic]` (speculative). Keep ≤ 9 words.

### Tone calibration

| Situation | Tone |
|---|---|
| Strong technical fit | Confident, direct, lead with results |
| Genuine stretch | Enthusiastic about the field first, honest about the gap second |
| Significant mismatch | Brief, respectful, low-key — don't oversell |
| After a positive reply | Warm, specific, match their energy |

### Things to avoid
- Sending the same generic email to multiple professors without adjusting
  research-specific content.
- Mentioning unrelated applications or other professors by name in an email
  to a different lab.
- Padding the email with excessive paragraphs — 4–5 tight paragraphs beats
  7 sprawling ones.
- Using em dashes, semicolons, or overly formal vocabulary if the situation
  calls for a natural, human tone — keep sentences varied and direct.

### Adapting per institution type
- **European universities (ETH, TUM, UZH, etc.)**: often require formal
  application via portal; email is for introduction only.
- **US universities**: email to professor is often the primary channel;
  mention applying through the department's PhD programme separately.
- **Industry-funded / lab-specific recruitment (LinkedIn)**: direct
  CV-to-email process; keep these slightly shorter and more conversational.
- **Form-based or coded applications** (e.g. ALPI Lab, AImageLab): the
  subject code is often used for automated sorting — get it exactly right.

---

## 3. Cover / Motivation letter

**Length:** 800–900 words. (Treat as a competitive artefact — committees
read these end-to-end; a thin cover letter ranks below a substantive one.)

**Applies to:** PhD positions, predoctoral positions, research
internships, and research assistant roles. Adapt the depth and emphasis
based on the position type (most depth for PhD, lightest for a 3-month
internship). The structure below is the same in all four cases.

**Voice.** Apply the British-English and "future researcher not job
applicant" rules from Section 0. Most of the letter should be about the
*research* — the gap, the method, the question — and only secondarily
about the candidate.

**Structure (7 paragraphs):**

1. **Salutation.** "Dear Professor {Surname}," — match the surname to the
   professor's name. No "Dear Sir/Madam".
2. **Motivation hook (~120 words).** Why THIS professor specifically —
   ground it in one verified paper cited by exact title. Say what the
   paper is doing methodologically and why that direction is the one Raj
   wants to pursue for 4 years. Not a generic "I am passionate about AI".
3. **Relevant background (~140 words).** What Raj has done that prepares
   them for this work. One named project from the profile (foreground
   project) with concrete, traceable result (a method, dataset, metric).
   No CV-list of every project — pick the one most relevant to the lab.
4. **Specific fit (~140 words).** The gap in the cited paper, the research
   question Raj would pursue, the method Raj would bring. Name the method
   (e.g. ViT, contrastive learning, SAE features, RAG), the dataset if
   relevant, and the evaluation approach. **Treat the gap as a question
   to be approached, not a problem to be solved in the letter.** A
   research direction in 2–3 sentences is enough; do not claim the gap
   is closed or the method is best.
5. **First-year contribution (~120 words).** Concrete: a 6–9 month plan,
   one or two deliverables (e.g. "a calibrated saliency map for the
   VinDr-Mammo scanner family, evaluated against radiologist consensus").
   This is the paragraph that distinguishes a strong cover letter from a
   generic one.
6. **Why this university / program (~100 words).** Name a specific resource
   (centre, lab, equipment, course, industry partner) that only this
   institution offers. Avoid generic "your excellent department".
7. **Ask + closing (~80 words).** Clear ask to be considered for the
   position; offer to share further materials; one-line availability for
   a 15-minute call. End with "Kind regards, Raj Kumar Sah".

---

## 4. Statement of Purpose

**Length:** 800–900 words. Multiple paragraphs (typically 7–9).

**Applies to:** PhD and predoctoral applications that explicitly request a
"Statement of Purpose" (or "personal statement"). For research internships
or research assistant roles, prefer the Cover / Motivation letter
structure unless the posting specifies an SOP.

**Voice.** Apply the British-English and "future researcher not job
applicant" rules from Section 0. The opening should locate the
intellectual motivation precisely; the middle should name a research gap
and propose a direction; the close should anchor the long-term academic
trajectory.

**Structure:**

1. **Opening motivation (~120 words).** A specific moment or question that
   pulled Raj into the research area — NOT a generic "since childhood". Tie
   it to a concrete artefact (a paper, a dataset, a model class) so the
   reader can locate the interest quickly.
2. **Academic background (~120 words).** Degree, institution, GPA if strong
   (>3.5/4 or equivalent), and 1–2 courses that shaped the trajectory.
   No transcript recital — name the courses that shifted the direction.
3. **Research experience (~180 words).** One named project (the foreground
   project) with concrete, traceable results: dataset name, method,
   evaluation metric, what was learned. If there are two relevant projects,
   split the paragraph; do not list four projects.
4. **Research interests and fit (~180 words).** Why this group specifically.
   Cite one verified paper by exact title. State the gap, the research
   question, and the approach Raj would take. Name the method. The gap
   is a *plausible next question*; the approach is a *direction*, not a
   solution. Avoid claiming the gap is closed, the method is novel, or
   the result is best — those claims belong in a paper, not in an SOP.
5. **Why this program / department (~100 words).** Specific resources
   (centre, lab, course, partner). Avoid generic praise.
6. **Career goals (~100 words).** Where Raj sees this PhD leading —
   academic, industry, or hybrid. Be concrete; "I want to make an impact"
   is not.
7. **Closing (~50 words).** Brief restatement of fit + a clear, modest
   close. "Kind regards, Raj Kumar Sah" is not appropriate for an SOP —
   end with a single line that reiterates fit.

---

## 4. Research Proposal

**Length:** 800–900 words. Use markdown section headings
(`Background`, `Research Gap`, `Research Question`, `Proposed Methodology`,
`Expected Contribution`, `Work Plan`, `References`).

**Section guidance:**

- **Background (~150 words).** The state of the art in 1–2 paragraphs.
  Reference the verified papers supplied in the prompt by exact title.
  Stay factual; this is not a literature review.
- **Research Gap (~120 words).** What is missing. Be specific — a method,
  a dataset, a population, an evaluation, a robustness check. The gap must
  follow from a cited paper.
- **Research Question (~80 words).** One clear question, stated as a
  sentence. Avoid compound questions.
- **Proposed Methodology (~220 words).** The method Raj would use: name it
  (ViT, contrastive learning, RAG, SAE features, …), the dataset, the
  evaluation protocol, and any baselines for comparison. This is the
  paragraph committees read most carefully — be concrete.
- **Expected Contribution (~100 words).** What the field gains if the work
  succeeds. Be specific; "advances the state of the art" is not a
  contribution.
- **Work Plan (~120 words).** A 12-month outline: 3 milestones, each with
  a deliverable and a rough month.
- **References (~50 words).** The cited papers listed by exact title +
  year + venue. Use the titles supplied in the prompt only.

---

## 6. Follow-up email

**Length:** 40–90 words.
**Subject:** Begin with "Re:" + the original subject.

**Rules:**
- Wait 1–2 weeks after the first email before following up.
- Maximum **two** follow-ups total. A third unanswered email starts to feel
  like pressure, not interest.
- Keep follow-ups shorter than the original — don't re-explain the whole
  background again.
- Polite nudge for an unanswered first-contact email. Reference the earlier
  email by subject. Restate the interest in one line.
- Do NOT introduce any new claims about Raj (no new projects, no new
  metrics).
- Do NOT re-attach documents.
- Warm but not pushy.
- If a deadline is approaching, you can reference it naturally
  ("ahead of the April 1st deadline") without sounding like a countdown
  threat.
- Second follow-up: even briefer, acknowledge they're busy, give them an
  easy out ("if the position is filled, no need to reply").
- After 2 follow-ups with no reply, stop. Don't send a third.