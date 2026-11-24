# PANXG exam import guide: `panxg-exam/v1` (Reading)

> **Dành cho giáo viên:** gửi cho AI (Gemini, ChatGPT, Claude…) file này cùng với đề gốc (PDF, ảnh hoặc text), rồi nhắn: *"Đọc hướng dẫn và chuyển đề này sang JSON panxg-exam/v1."* Dán kết quả vào màn **Import từ AI** trên PANXG. Nếu hệ thống báo lỗi, bấm **Sao chép lỗi**, gửi lại cho AI và nhờ nó sửa.

---

## 1. Your task (for the AI)

You receive a teacher's IELTS-style **Reading** test (passages, questions and, usually, an answer key). Convert it into **one JSON object** that follows the format below.

- Reply with **only** the JSON, inside one ```json code block. No commentary before or after.
- **Copy text exactly** as printed: passages, instructions, questions, options. Do not summarize, paraphrase, correct, translate or invent anything.
- Plain text only. No HTML, no Markdown inside strings. Use `\n` for a line break inside a string.
- If something is missing or unreadable, do **not** guess silently: mark the question `"uncertain": true` with a short `uncertain_reason`, or add a sentence to `ai_notes`.

## 2. Top level

```text
{
  "schema": "panxg-exam/v1",          // exactly this
  "skill": "reading",                  // exactly this
  "test": {
    "title": "...",                    // the test name, max 200 chars
    "test_type": "ielts_standard",     // "ielts_standard" (3 passages, 40 questions, 60 min) or "course"
    "duration_minutes": 60,            // 1-300
    "source_type": "self_authored"     // "self_authored" | "licensed" | "commercial" | "other"; if unsure use "other"
  },
  "sections": [ ... ],                 // one per passage, in order, 1-10
  "ai_notes": [ "..." ]                // optional: anything the teacher must check
}
```

No other fields are allowed anywhere; unknown fields are rejected.

## 3. Section (one passage and its questions)

```text
{
  "title": "Passage 1",                // as printed, or "Passage 1", "Passage 2"...
  "suggested_minutes": 20,             // optional
  "passage": {
    "title": "The history of glass",   // optional: the passage's own headline
    "paragraphs": [
      { "label": "A", "text": "..." }, // label only when the paragraph is lettered in the source
      { "text": "..." }
    ]
  },
  "groups": [ ... ]                    // question groups in printed order
}
```

Keep paragraphs as they appear. A paragraph's text is at most 10,000 characters; a passage at most 30,000.

## 4. Question group

A group is one printed block such as *"Questions 1-5 … Choose the correct letter, A, B, C or D."*

```text
{
  "type": "tfng",                      // see section 5
  "instructions": "Do the following statements agree with the information given in the reading passage? ...",
  "word_limit": 2,                     // completion types only: max words per answer ("NO MORE THAN TWO WORDS" -> 2)
  "options": [ { "key": "A", "text": "..." } ],   // shared list: matching, choose-N multiple choice
  "options_reusable": true,            // matching: true if "You may use any letter more than once"
  "select_count": 1,                   // 2 or 3 only for "Choose TWO/THREE letters"
  "prompt": "...",                     // only when select_count > 1: the shared question
  "content": "...",                    // notes / summary / table text with {{n}} where blank n is
  "questions": [ ... ]
}
```

Copy the instructions sentence in full, including phrases such as "NO MORE THAN TWO WORDS AND/OR A NUMBER". `word_limit` is only the word count.

## 5. Types

| `type` | Printed as | Answers are |
|---|---|---|
| `tfng` | TRUE / FALSE / NOT GIVEN | `TRUE`, `FALSE` or `NOT GIVEN` |
| `ynng` | YES / NO / NOT GIVEN | `YES`, `NO` or `NOT GIVEN` |
| `multiple_choice` | Choose the correct letter | option key(s) |
| `matching_headings` | List of headings i-x for paragraphs | heading key, e.g. `iv` |
| `matching_information` | Which paragraph contains… | paragraph letter |
| `matching_features` | Match each statement with a person/feature | option key |
| `sentence_endings` | Complete each sentence with the correct ending | option key |
| `sentence_completion` | Complete the sentences | words from the passage |
| `summary_completion` | Complete the summary (words or a box of options) | words, or the option key when a box of options is given |
| `note_completion` | Complete the notes | words |
| `table_completion` | Complete the table | words |
| `short_answer` | Answer the questions | words |

**Not supported:** pictures. For diagram or flow-chart labelling (`diagram_label`) do **not** use that type. Instead write the group as `note_completion`: describe each label in its question `prompt` using the words printed next to the blank (for example `"Label 10: the part that collects rainwater"`), keep the printed numbers, and add an `ai_notes` entry such as `"Questions 10-13 were a diagram; rewritten as notes, please check."` Numbering must stay 1..N without gaps.

## 6. Questions

```text
{
  "number": 1,                          // as printed; all numbers in the test run 1, 2, 3... with no gaps or repeats
  "prompt": "The statement or question text",
  "options": [ { "key": "A", "text": "..." } ],   // ONLY single-answer multiple choice with its own options
  "answers": ["TRUE"],                  // every accepted answer (see below)
  "uncertain": false,
  "uncertain_reason": "..."             // required in spirit when uncertain is true
}
```

Rules:

1. **Answers.** Take them from the answer key. If the key lists alternatives (`three / 3`, `(the) platform three`), put each accepted spelling as its own string: `["three", "3", "platform three"]`. For words in brackets, include the versions with and without them.
2. **No key?** Work the answer out from the passage, then set `"uncertain": true` and `"uncertain_reason": "No answer key; inferred from paragraph C."`.
3. **Matching / multiple choice** answers are option **keys** exactly as in `options` (`"C"`, `"iv"`), never the option text.
4. **Completion with a block of text** (notes, summary, table): put the text in the group's `content`, with `{{n}}` where blank number *n* is, and leave the question `prompt` out. For a table, write one row per line with ` | ` between cells; the first line is the header row.
5. **Choose TWO (or THREE) letters**: one group with `"select_count": 2`, the shared question in `prompt`, the letters in the group `options`, and **one question per mark** (e.g. numbers 14 and 15), each with **one** different correct key. Order does not matter; the system accepts them either way round.
6. Keep printed spelling, capitals and punctuation in answers; the system normalizes case and spacing when marking.

## 7. Checklist before you reply

- [ ] `schema` is `panxg-exam/v1`, `skill` is `reading`.
- [ ] Every printed question is present once, numbered 1..N without gaps.
- [ ] Every question has at least one answer; uncertain ones are flagged with a reason.
- [ ] Matching and multiple-choice answers are keys that exist in `options`.
- [ ] Completion answers respect `word_limit`.
- [ ] Each `{{n}}` in `content` belongs to a question in the same group.
- [ ] No extra fields, no HTML, nothing invented. Only one ```json block in the reply.

## 8. Complete example

A short, self-written example (2 passages, 12 questions) that covers the common shapes:

```json
{
  "schema": "panxg-exam/v1",
  "skill": "reading",
  "test": {
    "title": "Practice Reading: Cities and Bees",
    "test_type": "course",
    "duration_minutes": 30,
    "source_type": "self_authored"
  },
  "sections": [
    {
      "title": "Passage 1",
      "suggested_minutes": 15,
      "passage": {
        "title": "Rooftop gardens",
        "paragraphs": [
          { "label": "A", "text": "Rooftop gardens first became popular in northern European cities in the 1970s, when planners looked for ways to cool buildings in summer." },
          { "label": "B", "text": "Today, a typical green roof can lower the temperature of the room below by up to four degrees. The plants also slow down rainwater, which reduces flooding in the streets." },
          { "label": "C", "text": "However, the cost of strengthening an old roof remains high, and most new projects are therefore found on recently built offices." }
        ]
      },
      "groups": [
        {
          "type": "tfng",
          "instructions": "Do the following statements agree with the information given in Reading Passage 1? Write TRUE, FALSE or NOT GIVEN.",
          "questions": [
            { "number": 1, "prompt": "Rooftop gardens started in southern Europe.", "answers": ["FALSE"] },
            { "number": 2, "prompt": "Green roofs can help to reduce street flooding.", "answers": ["TRUE"] },
            { "number": 3, "prompt": "Most city councils pay for green roofs.", "answers": ["NOT GIVEN"] }
          ]
        },
        {
          "type": "matching_information",
          "instructions": "Reading Passage 1 has three paragraphs, A-C. Which paragraph contains the following information? You may use any letter more than once.",
          "options": [
            { "key": "A", "text": "Paragraph A" },
            { "key": "B", "text": "Paragraph B" },
            { "key": "C", "text": "Paragraph C" }
          ],
          "options_reusable": true,
          "questions": [
            { "number": 4, "prompt": "a reason why old buildings rarely have green roofs", "answers": ["C"] },
            { "number": 5, "prompt": "the original purpose of rooftop gardens", "answers": ["A"] }
          ]
        },
        {
          "type": "summary_completion",
          "instructions": "Complete the summary below. Choose NO MORE THAN TWO WORDS AND/OR A NUMBER from the passage for each answer.",
          "word_limit": 2,
          "content": "A green roof can make the room below up to {{6}} degrees cooler, and its plants slow down {{7}}.",
          "questions": [
            { "number": 6, "answers": ["four", "4"] },
            { "number": 7, "answers": ["rainwater"] }
          ]
        }
      ]
    },
    {
      "title": "Passage 2",
      "suggested_minutes": 15,
      "passage": {
        "title": "Urban bees",
        "paragraphs": [
          { "text": "Beekeeping in cities has grown quickly. City bees often produce more honey than country bees because parks and gardens offer a wider variety of flowers and fewer pesticides." },
          { "text": "Experts warn, however, that too many hives in one area can leave wild bees without enough food." }
        ]
      },
      "groups": [
        {
          "type": "multiple_choice",
          "instructions": "Choose the correct letter, A, B, C or D.",
          "questions": [
            {
              "number": 8,
              "prompt": "Why do city bees often produce more honey?",
              "options": [
                { "key": "A", "text": "They are a stronger species." },
                { "key": "B", "text": "They have a greater range of flowers." },
                { "key": "C", "text": "Beekeepers feed them sugar." },
                { "key": "D", "text": "Cities are warmer in winter." }
              ],
              "answers": ["B"]
            }
          ]
        },
        {
          "type": "multiple_choice",
          "instructions": "Choose TWO letters, A-E.",
          "select_count": 2,
          "prompt": "Which TWO advantages of city environments for bees are mentioned?",
          "options": [
            { "key": "A", "text": "more types of flowers" },
            { "key": "B", "text": "fewer predators" },
            { "key": "C", "text": "fewer pesticides" },
            { "key": "D", "text": "cleaner water" },
            { "key": "E", "text": "longer summers" }
          ],
          "questions": [
            { "number": 9, "answers": ["A"] },
            { "number": 10, "answers": ["C"] }
          ]
        },
        {
          "type": "table_completion",
          "instructions": "Complete the table below. Choose ONE WORD ONLY from the passage for each answer.",
          "word_limit": 1,
          "content": "Topic | Detail\nGrowth of city beekeeping | has grown {{11}}\nRisk | wild bees may lack {{12}}",
          "questions": [
            { "number": 11, "answers": ["quickly"] },
            { "number": 12, "answers": ["food"], "uncertain": true, "uncertain_reason": "The answer key page was cut off; inferred from paragraph 2." }
          ]
        }
      ]
    }
  ],
  "ai_notes": []
}
```
