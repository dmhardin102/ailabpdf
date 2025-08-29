from collections import defaultdict
import os
import re
from datetime import datetime
from flask import Flask, request, render_template, send_from_directory, send_file
from dotenv import load_dotenv
from google import genai

from parse import parse_labcorp_pdf
import fitz
import io
import base64
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

from json import load
with open('default_ranges.json') as f:
    DEFAULT_RANGES = load(f)

with open('common_aliases.json') as f:
    COMMON_TEST_ALIASES = load(f)


@app.route("/")
def upload():
    return render_template('upload.html.j2', year=datetime.now().year)

@app.route("/parse", methods=["POST"])
def parse():
    paths = []
    for fstorage in request.files.getlist('file'):
        fstorage.save(path := f"./uploads/{fstorage.filename}")
        paths.append(path)

    docs, dates = [], []
    for path in paths:
        with fitz.open(path) as doc:
            subject_metadata, sample_metadata, data = parse_labcorp_pdf(doc)

        rows = []
        for row in data:
            test_name_candidates = [
                f'{row["Test"]} {row["Units"]}',
                f'{row["Panel"]} - {row["Test"]}',
                row["Test"],
            ]

            low, high, qualitative, units = None, None, None, row.get('Units')
            for test_name in test_name_candidates:
                test_name = COMMON_TEST_ALIASES.get(test_name.casefold(), test_name)
                if test_name in DEFAULT_RANGES:
                    match DEFAULT_RANGES[test_name]:
                        case [qualitative, units]:
                            pass
                        case [low, high, units]:
                            pass
                        case [low, high, units, _, _, _]:
                            pass
                    break


            if row["Reference Interval"] is not None:
                if row["Reference Interval"].startswith("<"):
                    high = row["Reference Interval"].removeprefix("<")
                    low = "0"
                elif row["Reference Interval"].startswith(">"):
                    low = row["Reference Interval"].removeprefix(">")
                    high = None
                else:
                    parts = re.split(r"\s*[-\u2013]\s*", row["Reference Interval"])
                    if row['Reference Interval'].startswith(('-', '\u2013')) and len(parts) == 3:
                        low, high = f'-{parts[0]}{parts[1]}', parts[2]
                    else:
                        low, high = parts[0], parts[1] if len(parts) > 1 else None

            row_data = {
                "Original": row["Test"],
                "Standardized": test_name,
                "Value": row["Current Result"],
                "Units": units,
                "Low": low,
                "High": high,
                "Qualitative": qualitative,
                "Flag": row["Flag"],
            }
            rows.append({k: v if v is not None else "" for k, v in row_data.items()})
        docs.append(rows)
        dates.append(sample_metadata['Date Collected'])

    from datetime import datetime
    doc_data=sorted(zip(paths, docs, dates), key=lambda v: datetime.strptime(v[-1], '%m/%d/%Y'))


    return render_template(
        'verification.html.j2',
        doc_data=doc_data,
        DEFAULT_RANGES=DEFAULT_RANGES,
    )

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)


def iter_multidict_items(multidict):
    values = [multidict.getlist(key) for key in multidict.keys()]
    clean_keys = [k.removesuffix('[]') for k in multidict.keys()]
    for row in zip(*values):
        yield dict(zip(clean_keys, row))


@app.route("/final", methods=["POST"])
def final():
    import json

    final_rows = []

    # Hard-coded list of test names that should ALWAYS have "Not Established" reference ranges
    FORCE_NOT_ESTABLISHED = ["Neutrophils", "Monocytes", "Eos", "Lymphs", "Basophils", "Lymphocytes", "Eosinophils"]

    # Track which specific tests were explicitly flagged in the original PDF
    EXPLICITLY_FLAGGED = set()

    for row in iter_multidict_items(request.form):
        test = row.get("test-name", "").strip()
        val = row.get("correct-value", "").strip()
        units = row.get("correct-units", "").strip()
        low = row.get("correct-low", "").strip()
        high = row.get("correct-high", "").strip()

        # CRITICAL: Get the original flag from PDF and prioritize it completely
        original_flag = row.get("Flag", "").strip()

        # If a test was explicitly flagged in the original PDF, track it
        if original_flag in ["High", "Low", "Abnormal"]:
            EXPLICITLY_FLAGGED.add(test)

        # Direct fix for CBC percentage markers
        if (test in FORCE_NOT_ESTABLISHED and "Absolute" not in test and
            "Count" not in test and "abs" not in test.lower()):
            low = "Not Established"
            high = "Not Established"

            # IMPORTANT: Only keep original flag if it was present in the PDF
            flag = original_flag if original_flag else "Normal"
        else:
            # Only use default ranges if no valid ranges were provided
            if not low or not high or low == "N/A":
                # Try direct match first
                if test in DEFAULT_RANGES:
                    range_data = DEFAULT_RANGES[test]
                    default_low = range_data[0]
                    default_high = range_data[1]
                else:
                    # Try fuzzy matching
                    matched_key = None
                    best_match_score = 0

                    for key in DEFAULT_RANGES:
                        # Remove common variations for matching
                        test_clean = test.lower().replace(" ", "").replace(",", "").replace("(", "").replace(")", "")
                        key_clean = key.lower().replace(" ", "").replace(",", "").replace("(", "").replace(")", "")

                        # Check for substantial overlap
                        if test_clean in key_clean or key_clean in test_clean:
                            score = len(set(test_clean) & set(key_clean)) / len(set(test_clean) | set(key_clean))
                            if score > best_match_score:
                                matched_key = key
                                best_match_score = score

                    if matched_key and best_match_score > 0.5:  # Use threshold for good matches
                        range_data = DEFAULT_RANGES[matched_key]
                        default_low = range_data[0]
                        default_high = range_data[1]
                    else:
                        default_low, default_high = "N/A", "N/A"

                # Only use default ranges if they are not already set
                if not low or low == "N/A":
                    low = str(default_low)
                if not high or high == "N/A":
                    high = str(default_high)

            # CRITICAL CHANGE: ALWAYS preserve original flags from the PDF
            if original_flag:
                # Use the flag exactly as it came from the PDF
                flag = original_flag
            else:
                # Only calculate flags when no original flag exists
                flag = "Normal"
                try:
                    if low == "Not Established" or high == "Not Established" or low == "N/A" or high == "N/A":
                        flag = "Normal"
                    else:
                        obs = float(val)
                        try:
                            low_val = float(low)
                            if obs < low_val:
                                flag = "Low"
                        except:
                            pass

                        # Only check high value if it exists and is not empty
                        if high and high.strip():
                            try:
                                high_val = float(high)
                                if obs > high_val:
                                    flag = "High"
                            except:
                                pass
                except:
                    # Non-numeric values handling
                    if isinstance(val, str):
                        if any(x in val.lower() for x in ["neg", "negative", "none", "-"]):
                            flag = "Normal"
                        elif any(x in val.lower() for x in ["pos", "positive", "+"]):
                            flag = "High"
                        elif "high" in val.lower():
                            flag = "High"
                        elif "low" in val.lower():
                            flag = "Low"

        # This line should be inside the loop, indented to match the rest of the loop body
        final_rows.append({
            "Date": row['date'],
            "TestName": test,
            "ObservedValue": val,
            "Units": units,
            "Low": low,
            "High": high,
            "Flag": flag
        })

    # Define sort priority function
    def sort_priority(row):
        flag_priority = {flag: i for i, flag in enumerate(["High", "Low", "Abnormal"])}
        date = datetime.strptime(row["Date"], "%m/%d/%Y")
        return flag_priority.get(row["Flag"], len(flag_priority)), row["TestName"], date

    # Sort rows by priority and then name
    final_rows.sort(key=sort_priority)
    # Helper to pull numeric values for specific tests
    def get_numeric(test_name):
        for r in final_rows:
            if r["TestName"].casefold() == test_name.casefold():
                try:
                    return float(re.sub(r"[^0-9.\-]", "", r["ObservedValue"]))
                except Exception:
                    return None
        return None


    def get_numeric_any(*names):
        for name in names:
            val = get_numeric(name)
            if val is not None:
                return val
        return None

    triglycerides = get_numeric_any("Lipid Panel - Triglycerides", "Triglycerides")
    hdl = get_numeric_any("HDL-C", "HDL Cholesterol")
    insulin = get_numeric("Insulin")
    glucose = get_numeric("Glucose")
    a1c = get_numeric("Hemoglobin A1c")

    insulin_metrics = {
        "trig_hdl_ratio": round(triglycerides / hdl, 2) if triglycerides is not None and hdl not in [None, 0] else None,
        "homa_ir": round((insulin * glucose) / 405, 2) if insulin is not None and glucose is not None else None,
        "estimated_average_glucose": round(28.7 * a1c - 46.7, 2) if a1c is not None else None,
    }

    # Return the rendered template
    test_data = defaultdict(lambda: defaultdict(list))
    for row in final_rows:
        test = row['TestName']
        for k, v in row.items():
            if k == 'TestName':
                continue
            if not v.strip() or v.casefold() == 'n/a':
                v = None
            test_data[test][k].append(v)
    return render_template('finaltable.html.j2', rows=final_rows, year=datetime.now().year, testData=test_data, insulin_metrics=insulin_metrics)


@app.route("/ai_summary", methods=["POST"])
def ai_summary():
    data = request.get_json(force=True)
    rows = data.get("rows", [])
    metrics = data.get("insulin_metrics", {})

    lab_lines = [
        f"{r['TestName']}: {r['ObservedValue']} {r['Units']} (Low: {r['Low']}, High: {r['High']}, Flag: {r['Flag']})"
        for r in rows
    ]
    metric_lines = [f"{k}: {v}" for k, v in metrics.items() if v is not None]

    prompt = (
        "Please review included lab markers and calculated values with regard to the condition of insulin resistance. "
        "Please highlight the risk of insulin resistance based on these values, and also note any other lab findings "
        "that may be indicative of disease or require follow-up labs and physician discussion.\n\n"
        "Please provide a concise summary in 2-3 paragraphs.\n\n" +
        "Insulin Metrics:\n" + "\n".join(metric_lines) + "\n\nLab Values:\n" + "\n".join(lab_lines)
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[prompt],
        )
        summary = response.text
    except Exception as e:
        summary = f"Error generating summary: {e}"

    return {"summary": summary}


@app.route("/chart_report", methods=["POST"])
def chart_report():
    data = request.get_json(force=True)
    charts = data.get("charts", [])

    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    width, height = letter

    # Layout: two charts per page (top and bottom)
    margin_x = 40
    margin_y = 40
    # Reserve space for a page-drawn title above each chart image
    title_h = 22
    gap_y = 24

    slots_per_page = 2
    slot_height = (height - 2 * margin_y - gap_y) / slots_per_page
    max_w = width - 2 * margin_x

    def draw_chart_at(img_reader, title, slot_index):
        # slot_index: 0 (top) or 1 (bottom)
        y_top = height - margin_y - slot_index * (slot_height + gap_y)
        # Image fits below title inside slot
        img_w, img_h = img_reader.getSize()
        max_h = slot_height - title_h - 8
        scale = min(max_w / img_w, max_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        draw_x = margin_x
        draw_y = y_top - title_h - draw_h

        # Title centered above the image area
        c.setFont("Helvetica-Bold", 14)
        title_x_center = draw_x + (draw_w / 2)
        title_y = y_top - (title_h - 4)
        try:
            c.drawCentredString(title_x_center, title_y, title)
        except Exception:
            # Fallback to left-aligned if font metrics cause issues
            c.drawString(draw_x, title_y, title)

        # White background behind the image
        c.setFillColorRGB(1, 1, 1)
        c.rect(draw_x, draw_y, draw_w, draw_h, fill=1, stroke=0)
        c.drawImage(img_reader, draw_x, draw_y, draw_w, draw_h)

    i = 0
    while i < len(charts):
        # First chart on page
        chart = charts[i]
        name = chart.get("name", "")
        image = chart.get("image")
        if image:
            img_bytes = base64.b64decode(image.split(",", 1)[1])
            img = ImageReader(io.BytesIO(img_bytes))
            draw_chart_at(img, name, 0)
        i += 1

        # Second chart on same page if available
        if i < len(charts):
            chart2 = charts[i]
            name2 = chart2.get("name", "")
            image2 = chart2.get("image")
            if image2:
                img_bytes2 = base64.b64decode(image2.split(",", 1)[1])
                img2 = ImageReader(io.BytesIO(img_bytes2))
                draw_chart_at(img2, name2, 1)
            i += 1

        c.showPage()

    c.save()
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="lab_charts.pdf",
    )
