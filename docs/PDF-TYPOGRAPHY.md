# PDF typography

Read mode: an editor should find cut ranges, dialogue and review findings quickly.

- Embedded Roboto Regular and Bold, bundled in fonts/ with the SIL Open Font License. Rendering is offline; fonts are not fetched when exporting.
- A4 white pages with 60 pt side margins and restrained charcoal/amber accents.
- Collection title 32/38 pt; draft title 26/32; section heading 15/21; body 11/17; dialogue 12/19; metadata 9.5/14.
- Dialogue uses a shorter reading measure, left alignment and generous spacing between speakers. Original text is preserved and long paragraphs split across pages.
- Labels stay with the following text; draft titles stay with their status. The unanswered question appears near the start of each draft.
- Export keeps every draft, pending reviews, failures, exact source/playback ranges and spoiler findings. Formatting does not change validation or model output.

Font source: https://github.com/google/fonts/tree/main/ofl/roboto
The bundled static 400/700 weights were instantiated from the official variable font for ReportLab compatibility. Runtime dependencies do not include fontTools.
