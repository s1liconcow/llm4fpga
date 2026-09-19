This is the historical Q16 candidate, retained with its provenance because it
passed the kernel audit but failed the Intel trajectory comparison. Its coordinate
packing uses 16 fractional bits; the current `demo slam` contract uses 24 and is
incompatible with this fixture. See `docs/slam-q16-results.json` for the original
contract and all three integration outcomes, and
[the demo report](../../../docs/demo-results.md#what-pressure-testing-caught) for the diagnosis.
