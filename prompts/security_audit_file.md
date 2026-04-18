
You are performing a security audit of file `{{target_file}}`. Related header files and other source files can be found in `{{context_dir}}`. 

Use the tools available to you to read the target file and any headers it includes. Trace data flows, check macro definitions, and examine related functions as needed.

Your task:
1. Identify any security vulnerabilities in the code
2. For each vulnerability found, provide:
   - The specific file and line number(s)
   - The vulnerability type (e.g., CWE classification)

Focus on memory safety, input validation, and access control issues. Be thorough and precise.


If you find the vulnerability in `{{target_file}}`, report them as soon as possible. You do not need to analyze other files.


Output your findings as a JSON dictionary with the following structure:
[
{
  "file": "path/to/file.c",
  "line_start": 100,
  "line_end": 110,
  "vulnerability_type": "CWE-XXX: Description",
  "severity": "critical|high|medium|low",
  "description": "...",
}
...
]

In our output, only return a JSON. If you find no vulnerabilities, return an empty array `[]`.
