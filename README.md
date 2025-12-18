INPUT (put the following CSV into ./splose_export.csv): 
  Splose.com -> setting -> Data -> Export:
    Export Client forms
If your Splose does not have this feature, it probably means you don't have Export Access.

OUTPUT (to ./splose_flattened.csv by default):
  a tedious CSV that flatten the CSV in ./splose_export.csv

You may modify config.json, especially "form_titles" (which really should have been a built-in filter on Splose.com).

Attempt to modify splose_export_to_flat_csv.py can end up being an endless and hopelessness battle with AI vomitus.
