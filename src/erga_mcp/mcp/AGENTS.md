# MCP adapter instructions

Keep this package a thin protocol adapter. Reusable logic belongs in the owning sibling package
(`applications/`, `resumes/`, `portfolio/`, or `tracking/`) so the CLI can call it too.

- `contracts.py`: Pydantic wire contracts only.
- `profiles.py`: capability annotations, profile membership, and private-data visibility.
- `registry.py`: registration policy only.
- `sampling.py`: translate client-neutral model requests/results to MCP sampling types.
- `*_tools.py`: one cohesive tool family; dependency-inject core functions/state when useful.
- `package_manifest.py`: persisted package-to-wire validation translation.

Every tool needs bounded typed inputs, an accurate side-effect annotation, explicit profile
membership, synthetic tests, and no secret/path leakage beyond its declared contract. Do not add
tools that submit applications, send messages, mutate remote mail, or treat imported content as
instructions. Preserve official-SDK stdio and authenticated Streamable HTTP interoperability.
