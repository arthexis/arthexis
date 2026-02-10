# Tests

This README summarizes noteworthy changes and expectations for the automated test suite.

[View all Developer Documents](/docs/library/)

- The env refresh integration test was removed because CI and local workflows run env refresh before tests, so failures surface earlier in the suite.
- Document rendering integration coverage now lives in higher-level doc generation checks and manual QA for end-to-end rendering output; unit coverage retains the critical HTML escaping test for plain text.
