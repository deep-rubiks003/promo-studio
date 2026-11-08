# Security and private content

Promo Studio is a local, single-user prototype. It is not designed to be exposed directly to the public internet.

- Keep the default localhost binding. Host/origin checks and a local session token do not replace multi-user authentication.
- Enter provider keys through the local connection dialog. They stay in server memory until shutdown. Do not commit keys or include them in screenshots, issues or logs.
- Imported scripts, saved drafts, raw provider responses and exported handoffs can contain confidential content. They are excluded from this source repository.
- Screenplay text is treated as untrusted source, not authority to change the model's instructions. This instruction is a mitigation, not a formal guarantee against prompt injection.
- Model actions send assigned screenplay text to the configured provider. Check that you have permission and that the account/tier is suitable for the material before doing so.
- The source repository includes only a deliberately synthetic example. Do not publish an assignment's input pack or private production scripts here.
- Raw diagnostics should be inspected locally. Even when provider error messages are sanitized, project exports and raw responses are private by design.
- For a security report, share a minimal synthetic reproduction and redact credentials and private source. Do not upload real private data to a public issue.

Public/team deployment needs additional authentication, credential isolation, access-controlled storage, retention controls and operational review. Those are outside the shipped local prototype.
