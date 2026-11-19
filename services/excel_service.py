
import pandas as pd
from datetime import date, datetime
from decimal import Decimal


def get_sheet_names(file_path):

    excel = pd.ExcelFile(
        file_path,
        engine="openpyxl"
    )

    try:
        return excel.sheet_names
    finally:
        excel.close()


def normalize_value(value):

    if value is None:
        return None

    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if isinstance(value, (datetime, date)):
        return value

    if isinstance(value, Decimal):
        return str(value)

    return value


def normalize_columns(columns):

    result = []

    for column in columns:

        name = str(column).strip()

        if not name:
            raise ValueError(
                "Excel contains an empty column name."
            )

        result.append(name)

    normalized = [x.upper() for x in result]

    if len(normalized) != len(set(normalized)):
        raise ValueError(
            "Excel contains duplicate column names."
        )

    return result


def read_excel_data(
    file_path,
    sheet_name,
    preview_limit=30
):

    df = pd.read_excel(
        file_path,
        sheet_name=sheet_name,
        engine="openpyxl",
        dtype=object
    )

    df = df.dropna(how="all")

    df.columns = normalize_columns(df.columns)

    columns = list(df.columns)

    preview_df = df.head(preview_limit)

    preview_rows = []

    for _, row in preview_df.iterrows():

        item = {}

        for column in columns:

            value = normalize_value(row[column])

            item[column] = (
                "" if value is None else str(value)
            )

        preview_rows.append(item)

    return {
        "columns": columns,
        "rows": preview_rows,
        "total_rows": len(df),
        "preview_rows": len(preview_rows)
    }


def read_excel_records(
    file_path,
    sheet_name
):

    df = pd.read_excel(
        file_path,
        sheet_name=sheet_name,
        engine="openpyxl",
        dtype=object
    )

    df = df.dropna(how="all")

    df.columns = normalize_columns(df.columns)

    records = []

    for _, row in df.iterrows():

        record = {}

        for column in df.columns:

            record[column] = normalize_value(
                row[column]
            )

        records.append(record)

    return records
