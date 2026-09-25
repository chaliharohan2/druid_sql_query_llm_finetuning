# Addendum to the v2 plan: fixes before training

An owner-side audit of the v2 build found that table rules and glossary definitions repeat v1's template problem at the instruction level. This addendum lists the fixes to make before training. Everything in the v2 plan still applies unless this addendum says otherwise.

**Out of scope:** output-style drift (identifier quoting, alias casing) stays as it is. Do not touch training or inference code.

---

## How to work: code first, Gemini only when needed

Gemini calls are slow, so do everything you can in code. You can write the paraphrase banks, term lists and definition sentences yourself.

| Do in code (no Gemini) | Use Gemini only for |
|---|---|
| Rule and glossary paraphrase banks, the term vocabulary, heading layouts | **New** rows that need a new question and new SQL (F3, F4) |
| Re-rendering existing rows (F1, F2, F5) | G4 independent answers and the G9 judge on **those new rows only** |
| Term substitution in question text | Rewriting a question only when code substitution fails **and** dropping the row would leave a category short |
| Cleanup (F6), deterministic gates (G0, G2, G3, G5, G6, G7, G8), assembly, report | — |

- **Don't re-run G1, G4 or G9 on rows whose SQL is unchanged.** F1, F2 and F5 only change prompt or question text, and they preserve meaning by construction.
- **Order of work:**
  1. Do all the code-only fixes and rebuild.
  2. Run **one** batched Gemini pass for F3 and F4.
  3. Assemble and regenerate the report.
- **Apply F1, F2 and F5 to every split**, not only train, so the eval sets match the training distribution. Keep row ids stable.
- **Finish before the owner hand-verifies `test_prodlike`.**

---

## F1 · Reword rule sentences and vary their layout (code)

**Problem.** About six fixed sentences produce nearly all table rules. For example:
- "Use an exact `COUNT(DISTINCT …)`…" (3,789 occurrences)
- "exclude test records" (1,379)
- "exclude soft-deleted" (1,059)
- "duration … IS NOT NULL" (1,017)

The model can learn to recognise these sentences instead of learning to follow instructions. Real prompts use free-form prose under headings such as `### INSTRUCTIONS`. No v2 enterprise prompt has a rules heading.

**Do.**
- Write a paraphrase bank of **≥ 10 phrasings per rule type**. Vary register and length, with and without backticks, imperative and descriptive. Include some that fold two constraints into one sentence.
- Render the rules in **≥ 5 layouts**:
  - no heading (as now);
  - `### INSTRUCTIONS` or `### Instructions`, with `*` bullets;
  - a `**Rules**` block;
  - a numbered list;
  - rules embedded in the overview paragraph.
- Sample with a fixed seed. The constraint metadata (`required_predicates`, `forbidden_constructs`) doesn't change, so G6 still applies unchanged.

**Accept.**
- No single phrasing exceeds 5% of occurrences of its rule type.
- Each layout covers ≥ 10% of prompts that show rules.
- G6 passes on 100% of rows.

## F2 · Rename glossary terms and vary definition wording (code)

**Problem.**
- Every definition has the same shape: "A *term* is one where `col` is `v1`, `v2`".
- Every term starts with *flagged*, *priority*, *notable* or *key* (6,863 definition occurrences).
- Almost every question using those words has a matching definition: 427 of 444 for "priority", 470 of 473 for "key".

The model learns that these words trigger an arbitrary filter. In the owner's real data, `priority` is a real column and "key" is ordinary English.

**Do.**
- Replace the terms with **domain-specific phrases** that you write per domain, e.g. "rush shipment", "VIP booking", "at-risk account", "escalated ticket", "problem flight".
- Update the question text to match: handle plurals, case, and possessives. If a row can't be substituted cleanly in code, **drop it**. Queue it for a Gemini rewrite only if dropping would leave a category short.
- Write **≥ 8 definition sentence patterns**, for example:
  - "X means …"
  - "We treat … as X"
  - "X: rows where …"
  - "Count something as X when …"
  - a `Term | Definition` table
  - a `**Glossary**` block

**Accept.**
- **≥ 300** distinct terms across the dataset.
- No head word in more than 3% of definitions.
- *flagged*, *priority*, *notable* and *key* together in ≤ 10% of definitions.
- No definition pattern in more than 20% of definitions.
- The G7 style check still passes, and every glossary term used in a question appears in that prompt's glossary.

## F3 · Richer definition shapes (Gemini: new rows)

**Problem.** Real glossaries define terms with logic far beyond a single value list.

**Do.** Generate **≥ 500 new train rows**, at least 50 per shape. Put about 40 more into the held-out eval pools, targeting held-out families only. Shapes:
- OR across two columns (`col_a = 'x' OR col_b = 42`);
- `NOT IN`;
- `LIKE` or prefix match;
- numeric thresholds;
- mixed AND/OR;
- time-based definitions ("active = at least one event in the last 90 days");
- derived-metric definitions ("handle time = end minus answer, in seconds");
- glossaries with several terms where only one applies to the question.

Every new row goes through the full G0–G9 pipeline.

**Accept.** All shapes present in the target counts; every new row passes G0–G9.

## F4 · Decoys: glossary-like words with no glossary meaning (Gemini: new rows)

**Do.** Generate **≥ 250 train rows** where words like *key*, *priority*, *flagged*, *notable*, *critical* or *premium* appear in the question but **no definition applies**:
- Ordinary English: "what are the key trends…".
- **≥ 100 rows** where the word refers to a **real column**: e.g. "P1 priority cases" means `priority = 'P1'`, and "critical severity" means `severity = 'critical'`.
- Some rows where a glossary exists but defines a different term.

Gold SQL must not add any glossary filter.

**Accept.**
- Of all train questions containing those six words, ≤ 60% have a matching definition in the prompt.
- 0 decoy rows apply a glossary filter.

## F5 · Nullability closer to real prompts (code)

**Problem.** About 90% of column annotations say NOT NULL. In the owner's real schemas, almost every column except `__time` is NULLABLE.

**Do.**
- Re-render annotations so **60–80% of non-`__time` columns are NULLABLE**. `__time` stays NOT NULL.
- You may flip columns the gold SQL references, **except** columns used in duration or epoch arithmetic where the gold SQL has no `IS NOT NULL` guard. Flipping those would contradict CONVENTIONS.md, so leave them as they are.
- The NULLABLE share among **referenced** columns must be within 5 points of the share among **unreferenced** columns. Otherwise the annotation itself becomes a hint about which columns matter.

**Accept.** Both shares are reported, and both conditions hold.

## F6 · Cleanup (code)

- **Questions with SQL or backticks.** Drop the ~138 questions containing SQL fragments or backticks (e.g. "grouped by the TIME_FLOOR of __time"). Detect them in code. Rewrite with Gemini only if a category would fall below its target.
- **Duplicates.** Deduplicate the 12 exact-duplicate questions, keeping one of each.
- **Hidden epoch units.** Drop rows whose gold SQL assumes a unit for a BIGINT time column when the prompt gives no unit: no unit in the description, the name or the sample rows. Known cases are `v2_train_01551` and `v2_train_03459`. Add this check as a gate so it runs on the new rows too.
- **Stale seed data in `score_predictions.py`.** Rows where the gold result is empty must count as **unscorable**, not as a match; report how many there are. Also print a warning when the seed-data anchor is more than 14 days old. Stale data makes both gold and prediction return nothing for relative windows, which currently scores as a match.

---

## Report additions

Add these to `dataset_report.md`:
- rule-phrasing and layout shares (F1);
- distinct glossary terms, head-word shares and definition-pattern shares (F2);
- counts per definition shape (F3);
- decoy counts, and the matching-definition share for the six words (F4);
- NULLABLE shares, referenced vs unreferenced (F5);
- F6 drop counts;
- the Gemini calls and cost for this pass, and the number of rows rewritten by Gemini versus handled in code.

The "must be zero" table must stay all zeros.