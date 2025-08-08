# Lab Report Parser and Visualizer

This tool will faciliate tabulation and charting of test results from uploading a pdf of their results voluntarily.

Step 1: Ensure accurate parsing from pdf into table. Auto-detection preferred, but review and confirmation by Insider warranted before export or storage.

Raw and Standardized columns for variables detected (Test Name, Symbol, Unit, Reference Range, Flag)

Key outcome is to associate a value in the report with the correct test name. The unit, reference range, and resultant flag can reference standardized JSON file.

Visualization of single test results in charts with annotation highlighting abnormal values.

Upload multiple pdfs for trending changes in biomarkers over time. Dates and results can be auto-detected, but confirmed by manually before export or storage.

Trends can be hghlighted with annotations.

Next steps: calculate HDL/Trig ratio, HOMA-IR, and A1c estimated average glucose.
Secure app via user authentication and encrypted storage of data.
Securely interact with LLM to generate plain language summaries of results and trends.
