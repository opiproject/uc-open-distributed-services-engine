# functional

Functional correctness tests for ODSE components: does the stack do what
it is configured to do, independent of performance.

Cases: [`docs/test-plan/functional-test-plan.md`](../../docs/test-plan/functional-test-plan.md)
(FN-01…FN-18).

Expected contents:

- End-to-end connectivity tests across OVS/OVN, OVN-Kubernetes, and
  OpenStack-managed networks
- Role-level functional checks (e.g. SR-IOV VF creation, switchdev mode,
  vDPA device presence)
- Test fixtures/harness shared with `ci/molecule` and `ci/kind-simulated-dpu`
