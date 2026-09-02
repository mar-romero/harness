# Quality, testing and security

Tests should fail for the defect they claim to detect. Control clocks, randomness,
environment and network. Validate boundary data. Use least privilege. Avoid
secret material in code/logs/fixtures. Treat deserialization, template rendering,
command construction, SQL, file paths and remote content as trust boundaries.
