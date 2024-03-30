import requests
import openpyxl
from io import BytesIO
import json

url = 'https://docs.google.com/spreadsheets/d/1Pzd0-8KqRsZZgdXCJQJ8Nq1TAmz-TEGusOZKlgG7RDk/export?format=xlsx'

def download_and_parse_xlsx():
    # Download the file
    resp = requests.get(url)
    filtered_data = []  # List to hold dictionaries of filtered data
    if resp.status_code == 200:
        # Open the downloaded .xlsx file
        workbook = openpyxl.load_workbook(filename=BytesIO(resp.content), data_only=True)
        sheet = workbook.active  # Assumes data is in the first sheet
        
        # Initialize variables to determine if we are in a data section
        in_data_section = False
        headers = []
        
        # Iterate over rows
        for row in sheet.iter_rows(values_only=True):
            # Skip empty rows
            if not any(row):
                in_data_section = False
                continue

            # If the first cell in the row is 'Route name', this is a header
            if row[0] == 'Route name':
                in_data_section = True
                headers = [cell for cell in row if cell]  # Capture the headers
                continue

            # Skip rows if not in data section or if there are no headers
            if not in_data_section or not headers:
                continue

            # Create a dictionary for each row with the header as the key
            row_data = dict(zip(headers, row))

            # Get the distance value and ensure it is a number
            distance = row_data.get('Distance (mi)')
            if isinstance(distance, (int, float)) and 20 <= distance <= 50:
                filtered_data.append(row_data)  # Add the row's data to the list
    
    # Convert the list of dictionaries to JSON
    json_data = json.dumps(filtered_data, ensure_ascii=False)
    return json_data

# # Replace 'url' with your direct download link
# json_result = download_and_parse_xlsx()
# print(json_result)
