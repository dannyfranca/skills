# Test-Coverage Review

Changed runtime behavior needs regression evidence proportionate to its risk. Verify tests would
fail if the implementation regressed across meaningful success, failure, boundary, and integration
paths.

Review observable behavior through public seams. Avoid tests coupled to private implementation details, tautological assertions, and mocks that bypass the behavior under review. Test-only changes do not require tests for tests.
