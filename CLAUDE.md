# Compact Instructions

When you want to execute a particular bash command, remember:
- This project has a virtual environment activation script located at .venv/bin/activate. However before you source it, you have to run "conda deactivate" first.
- Save the bash script to a file and tell user to execute it
- Do not do this yourself by using execution tool, just tell user what to do

When compacting, always preserve:
- All modified file paths with line numbers
- Current test results (pass/fail with file names)
- The active task plan and remaining TODO items
- Error messages and stack traces from the current debug session
- Architecture decisions with their reasoning
