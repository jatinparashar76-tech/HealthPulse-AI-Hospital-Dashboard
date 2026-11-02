import os
import json
import joblib
import pandas as pd
import numpy as np
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__, template_folder='templates', static_folder='static')

CSV_PATH = 'hospital_data_cleaned.csv'
READMISSION_MODEL_PATH = 'models/readmission_model.joblib'
SATISFACTION_MODEL_PATH = 'models/satisfaction_model.joblib'

# In-memory cached dataset & models
df_cache = None
readmit_model = None
sat_model = None

def get_data():
    global df_cache
    if df_cache is None or not os.path.exists(CSV_PATH):
        if os.path.exists(CSV_PATH):
            df_cache = pd.read_csv(CSV_PATH)
        else:
            df_cache = pd.DataFrame()
    return df_cache

def load_models():
    global readmit_model, sat_model
    if readmit_model is None and os.path.exists(READMISSION_MODEL_PATH):
        readmit_model = joblib.load(READMISSION_MODEL_PATH)
    if sat_model is None and os.path.exists(SATISFACTION_MODEL_PATH):
        sat_model = joblib.load(SATISFACTION_MODEL_PATH)

# Initial load
get_data()
load_models()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/metadata', methods=['GET'])
def get_metadata():
    df = get_data()
    conditions = sorted(df['Condition'].dropna().unique().tolist())
    procedures = sorted(df['Procedure'].dropna().unique().tolist())
    genders = sorted(df['Gender'].dropna().unique().tolist())
    outcomes = sorted(df['Outcome'].dropna().unique().tolist())
    
    # Map condition to default procedure & cost
    cond_map = {}
    for cond in conditions:
        subset = df[df['Condition'] == cond]
        if not subset.empty:
            proc = subset['Procedure'].iloc[0]
            cost = int(subset['Cost'].iloc[0])
            cond_map[cond] = {"procedure": proc, "default_cost": cost}

    return jsonify({
        "conditions": conditions,
        "procedures": procedures,
        "genders": genders,
        "outcomes": outcomes,
        "condition_mappings": cond_map
    })

@app.route('/api/kpis', methods=['GET'])
def get_kpis():
    df = get_data()
    total = len(df)
    if total == 0:
        return jsonify({})

    total_cost = int(df['Cost'].sum())
    avg_cost = round(float(df['Cost'].mean()), 2)
    avg_stay = round(float(df['Length_of_Stay'].mean()), 1)
    readmit_count = int((df['Readmission'] == 'Yes').sum())
    readmit_rate = round((readmit_count / total) * 100, 2)
    avg_sat = round(float(df['Satisfaction'].mean()), 2)
    recovered_count = int((df['Outcome'] == 'Recovered').sum())
    recovered_rate = round((recovered_count / total) * 100, 2)

    gender_dist = df['Gender'].value_counts().to_dict()
    outcome_dist = df['Outcome'].value_counts().to_dict()

    return jsonify({
        "total_patients": total,
        "total_cost": total_cost,
        "avg_cost": avg_cost,
        "avg_stay": avg_stay,
        "readmission_count": readmit_count,
        "readmission_rate": readmit_rate,
        "avg_satisfaction": avg_sat,
        "recovered_rate": recovered_rate,
        "gender_distribution": gender_dist,
        "outcome_distribution": outcome_dist
    })

@app.route('/api/charts', methods=['GET'])
def get_charts():
    df = get_data()
    if df.empty:
        return jsonify({})

    # 1. Volume by condition
    cond_counts = df['Condition'].value_counts()

    # 2. Readmission rate by condition
    cond_readmit = (df.groupby('Condition')['Readmission_Flag'].mean() * 100).round(1).loc[cond_counts.index]

    # 3. Cost by procedure
    proc_costs = df.groupby('Procedure')['Cost'].mean().round(0).sort_values(ascending=False)

    # 4. Satisfaction by Age Group
    age_sat = df.groupby('Age_Group', observed=False)['Satisfaction'].mean().round(2)

    # 5. Length of Stay categories
    los_dist = df['LOS_Category'].value_counts()

    return jsonify({
        "condition_labels": cond_counts.index.tolist(),
        "condition_volumes": cond_counts.values.tolist(),
        "condition_readmit_rates": cond_readmit.values.tolist(),
        "procedure_labels": proc_costs.index.tolist(),
        "procedure_costs": proc_costs.values.tolist(),
        "age_group_labels": age_sat.index.tolist(),
        "age_group_satisfaction": age_sat.values.tolist(),
        "los_labels": los_dist.index.tolist(),
        "los_volumes": los_dist.values.tolist()
    })

@app.route('/api/patients', methods=['GET'])
def get_patients():
    df = get_data()
    if df.empty:
        return jsonify({"data": [], "total": 0, "page": 1, "total_pages": 1})

    # Filters
    search = request.args.get('search', '').strip().lower()
    gender = request.args.get('gender', '').strip()
    condition = request.args.get('condition', '').strip()
    readmission = request.args.get('readmission', '').strip()
    outcome = request.args.get('outcome', '').strip()

    filtered = df.copy()

    if search:
        filtered = filtered[
            filtered['Patient_ID'].astype(str).str.contains(search) |
            filtered['Condition'].str.lower().str.contains(search) |
            filtered['Procedure'].str.lower().str.contains(search)
        ]

    if gender:
        filtered = filtered[filtered['Gender'] == gender]
    if condition:
        filtered = filtered[filtered['Condition'] == condition]
    if readmission:
        filtered = filtered[filtered['Readmission'] == readmission]
    if outcome:
        filtered = filtered[filtered['Outcome'] == outcome]

    total_records = len(filtered)
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 12))
    total_pages = max(1, (total_records + limit - 1) // limit)

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_data = filtered.iloc[start_idx:end_idx].to_dict(orient='records')

    return jsonify({
        "data": page_data,
        "total": total_records,
        "page": page,
        "total_pages": total_pages
    })

@app.route('/api/patients', methods=['POST'])
def add_patient():
    global df_cache
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    df = get_data()
    new_id = int(df['Patient_ID'].max() + 1) if not df.empty else 1001

    age = int(data.get('age', 45))
    gender = data.get('gender', 'Female')
    condition = data.get('condition', 'Appendicitis')
    procedure = data.get('procedure', 'Appendectomy')
    cost = int(data.get('cost', 8000))
    length_of_stay = int(data.get('length_of_stay', 4))
    readmission = data.get('readmission', 'No')
    outcome = data.get('outcome', 'Recovered')
    satisfaction = int(data.get('satisfaction', 4))

    # Derived engineered fields
    readmit_flag = 1 if readmission == 'Yes' else 0
    outcome_rec = 1 if outcome == 'Recovered' else 0
    cost_per_day = round(cost / length_of_stay, 2) if length_of_stay > 0 else cost

    age_group = 'Young Adult (<40)' if age < 40 else ('Middle-Aged (40-59)' if age <= 59 else 'Senior (60+)')
    los_cat = 'Short Stay (1-7d)' if length_of_stay <= 7 else ('Medium Stay (8-30d)' if length_of_stay <= 30 else 'Long Stay (31+d)')

    new_row = {
        'Patient_ID': new_id,
        'Age': age,
        'Gender': gender,
        'Condition': condition,
        'Procedure': procedure,
        'Cost': cost,
        'Length_of_Stay': length_of_stay,
        'Readmission': readmission,
        'Outcome': outcome,
        'Satisfaction': satisfaction,
        'Readmission_Flag': readmit_flag,
        'Outcome_Recovered': outcome_rec,
        'Cost_per_Day': cost_per_day,
        'Age_Group': age_group,
        'LOS_Category': los_cat
    }

    df_cache = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df_cache.to_csv(CSV_PATH, index=False)

    return jsonify({"success": True, "patient": new_row}), 201

@app.route('/api/predict', methods=['POST'])
def predict_clinical():
    load_models()
    if readmit_model is None or sat_model is None:
        return jsonify({"error": "ML models not ready"}), 500

    data = request.json or {}
    age = int(data.get('age', 55))
    gender = data.get('gender', 'Female')
    condition = data.get('condition', 'Heart Attack')
    cost = int(data.get('cost', 18000))
    length_of_stay = int(data.get('length_of_stay', 7))
    outcome = data.get('outcome', 'Stable')

    # 1. Readmission prediction input
    input_readmit = pd.DataFrame([{
        'Age': age,
        'Gender': gender,
        'Condition': condition,
        'Cost': cost,
        'Length_of_Stay': length_of_stay,
        'Outcome': outcome
    }])

    readmit_prob = float(readmit_model.predict_proba(input_readmit)[0][1])
    readmit_pred = int(readmit_model.predict(input_readmit)[0])

    if readmit_prob >= 0.75:
        risk_tier = "CRITICAL"
        risk_color = "#e11d48"
    elif readmit_prob >= 0.40:
        risk_tier = "MODERATE"
        risk_color = "#f59e0b"
    else:
        risk_tier = "LOW"
        risk_color = "#10b981"

    # 2. Satisfaction prediction input
    readmit_str = "Yes" if readmit_pred == 1 else "No"
    input_sat = pd.DataFrame([{
        'Age': age,
        'Gender': gender,
        'Condition': condition,
        'Cost': cost,
        'Length_of_Stay': length_of_stay,
        'Readmission': readmit_str,
        'Outcome': outcome
    }])

    sat_pred = round(float(sat_model.predict(input_sat)[0]), 1)
    sat_pred = max(1.0, min(5.0, sat_pred))

    # 3. Clinical recommendations based on prediction
    recs = []
    if risk_tier in ["CRITICAL", "MODERATE"]:
        recs.append("Schedule proactive tele-health follow-up within 48 to 72 hours of discharge.")
        recs.append("Conduct comprehensive medication reconciliation with primary care physician.")
    if cost >= 10000:
        recs.append("Provide itemized financial counseling and patient billing navigation prior to discharge.")
    if age >= 60:
        recs.append("Assign a dedicated senior patient care advocate to support recovery transition.")
    if outcome != "Recovered":
        recs.append("Enroll patient in specialized outpatient monitoring and rehabilitation program.")
    if not recs:
        recs.append("Standard post-discharge recovery guidance and routine 30-day check-in.")

    return jsonify({
        "readmission_prediction": readmit_pred,
        "readmission_probability": round(readmit_prob * 100, 1),
        "risk_tier": risk_tier,
        "risk_color": risk_color,
        "predicted_satisfaction": sat_pred,
        "recommendations": recs
    })

@app.route('/api/export', methods=['GET'])
def export_csv():
    if os.path.exists(CSV_PATH):
        return send_file(CSV_PATH, as_attachment=True, download_name='hospital_data_cleaned.csv')
    return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    print("HealthPulse AI Hospital Intelligence Server starting on http://127.0.0.1:5000")
    app.run(host='127.0.0.1', port=5000, debug=False)
