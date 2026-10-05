# Toy relay

## Progress at a glance
- [ ] **Phase 1 — Count one**
- [ ] **Phase 2 — Count two**
- [ ] **Phase 3 — Summary**

## Goal
A tiny relay that proves handoffs work. Each phase writes one small file.

## Locked decisions
1. Word counts are whitespace-separated tokens.

## Phases
### Phase 1 — Count one
- [ ] Write `notes/one.md` containing exactly: `one.txt has N words` (N = word count of data/one.txt).
- **Definition of done:** the file exists with the right number.

### Phase 2 — Count two
- [ ] Write `notes/two.md` containing exactly: `two.txt has N words` for data/two.txt.
- **Definition of done:** the file exists with the right number.

### Phase 3 — Summary
- [ ] Write `notes/summary.md` containing `total N words` (sum of phases 1 and 2), then finish the relay.
- **Definition of done:** the file exists with the right total.

## Constraints
- Do not touch `data/`. This is a test: keep every reply short.
