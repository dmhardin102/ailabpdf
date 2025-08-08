# rlinsiders

This tool will faciliate tabulation and charting of test results from uploading a pdf of their results voluntarily.

Step 1: Ensure accurate parsing from pdf into table. Auto-detection preferred, but review and confirmation by Insider warranted before export or storage.

Raw and Standardized columns for variables detected (Test Name, Symbol, Unit, Reference Range, Flag)

Key outcome is to associate a value in the report with the correct test name. The unit, reference range, and resultant flag can reference standardized JSON file. 

upapp.py code developed for parsing, table creation, and charting for single pdf. 

Desire to merge trend code to single pdf upapp.py for full solution.

Step 2: Visualization of single test results in charts with annotation highlighting abnormal values.

Step 3: Upload multiple pdfs for trending changes in biomarkers over time. Dates and results can be auto-detected, but confirmed by manually before export or storage.

Step 4: Secure login mechanism to view stored pdfs, reports, and trends with mobile and web application.
