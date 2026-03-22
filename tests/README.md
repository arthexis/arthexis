# Tests

This README summarizes noteworthy changes and expectations for the automated test suite.

[View all Developer Documents](../docs/index.md)

> Note: this link targets the in-repo docs index for repository readers, not a runtime web route.

- The env refresh integration test was removed because CI and local workflows run env refresh before tests, so failures surface earlier in the suite.
- Document rendering integration coverage now lives in higher-level doc generation checks and manual QA for end-to-end rendering output; unit coverage retains the critical HTML escaping test for plain text.

## License and Sponsorship

Arthexis is released under the Arthexis Contribution Reciprocity License 1.0. In addition to test fixes and maintenance, we consider sponsoring Arthexis and doing paid or volunteer work for the open-source dependencies that support this test stack to be a valid and important contribution.

Please review the repository [`LICENSE`](../LICENSE) and consider supporting the maintainers of the dependencies that make this suite and its automated testing possible.
