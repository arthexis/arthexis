# TODO Fixture Checklist

Use this checklist to decide when to add `Todo` fixtures so release management and GUI validation tasks are consistently tracked. Each item references the fixture-only workflow for the `Todo` model.

## Release Manager Tasks
- [ ] When a user reports a repeated error or regression—explicitly or implicitly—create a Release manager `Todo` fixture describing the follow-up work, regardless of whether the regression affects the UI.
- [ ] Include the relevant `url` for the resource or admin page whenever one exists.
- [ ] Provide clarifying context in `request_details` if no direct URL applies.

## GUI Validation
- [ ] After modifying any view, template, or other GUI element, add a `Todo` fixture titled `Validate screen [Screen]`.
- [ ] Set the `url` for the fixture to the screen requiring manual validation.

## Stub Completion
- [ ] When introducing stub code, raise a `NotImplemented` exception in the implementation.
- [ ] Create a matching `Todo` fixture to track completion of the stub, summarizing the remaining work.

Keep each `Todo` fixture in its own file, use natural keys instead of numeric primary keys, and follow the repository conventions for storing fixtures.
