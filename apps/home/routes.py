# -*- encoding: utf-8 -*-
import wtforms
from apps.home import blueprint
from flask import render_template, request, redirect, url_for
from flask_login import login_required
from jinja2 import TemplateNotFound
from flask_login import login_required, current_user
from apps import db, config
from apps.models import *
from apps.tasks import *
from apps.authentication.models import Users
from flask_wtf import FlaskForm
import json
import os
from flask import Blueprint, request, jsonify
import requests
from datetime import datetime
import threading
import time
import requests
import time, base64, tempfile
from pathlib import Path
from flask import send_file

from apps.api.agent_state import get_all_agents, get_agent
from apps.api.threat_intel import get_recent_conns

AGENTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "agents.json")



ONLINE_WINDOW = 30  # сек

@blueprint.route('/')
@blueprint.route('/index')
def index():
    agents = get_all_agents()
    now_ts = time.time()

    # добавим вычисляемое поле для таблицы
    agents_view = []
    for a in agents:
        last_ts = a.get('last_seen_ts', 0)
        computed_online = (now_ts - last_ts) < ONLINE_WINDOW
        a_view = dict(a)
        a_view['computed_online'] = computed_online
        agents_view.append(a_view)

    active_count = sum(1 for a in agents if (now_ts - a.get('last_seen_ts', 0)) < ONLINE_WINDOW)
    inactive_count = len(agents) - active_count

    return render_template(
        'pages/index.html',
        agents=agents_view,                # важно: отдаём с computed_online
        active_count=active_count,
        inactive_count=inactive_count,
        segment='dashboard',
        parent='dashboard',
        title='HOME'
    )

@blueprint.route('/agent/<agent_id>')
def view_agent(agent_id):
    agent = get_agent(agent_id)
    if not agent:
        return "Agent not found", 404

    # ДОБАВЬ: подтянуть последние соединения
    conns = get_recent_conns(agent_id, limit=50)

    return render_template(
        'pages/agent_detail.html',
        agent=agent,
        agent_id=agent_id,
        exec_output=None,
        segment='agent_detail',
        conns=conns,                # <<< ПЕРЕДАЁМ В ШАБЛОН
    )

def load_agents():
    if os.path.exists(AGENTS_FILE):
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

@blueprint.route('/tables')
def tables():
    context = {
        'segment': 'tables'
    }
    return render_template('pages/tables.html', **context)

@blueprint.route('/notifications')
def notifications():
    context = {
        'segment': 'notifications'
    }
    return render_template('pages/notifications.html', **context)


@blueprint.route('/template')
def template():
    context = {
        'segment': 'template'
    }
    return render_template('pages/template.html', **context)


@blueprint.route('/landing')
def landing():
    context = {
        'segment': 'landing'
    }
    return render_template('pages/landing.html', **context)


def getField(column): 
    if isinstance(column.type, db.Text):
        return wtforms.TextAreaField(column.name.title())
    if isinstance(column.type, db.String):
        return wtforms.StringField(column.name.title())
    if isinstance(column.type, db.Boolean):
        return wtforms.BooleanField(column.name.title())
    if isinstance(column.type, db.Integer):
        return wtforms.IntegerField(column.name.title())
    if isinstance(column.type, db.Float):
        return wtforms.DecimalField(column.name.title())
    if isinstance(column.type, db.LargeBinary):
        return wtforms.HiddenField(column.name.title())
    return wtforms.StringField(column.name.title()) 


@blueprint.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():

    class ProfileForm(FlaskForm):
        pass

    readonly_fields = Users.readonly_fields
    full_width_fields = {"bio"}

    for column in Users.__table__.columns:
        if column.name == "id":
            continue

        field_name = column.name
        if field_name in full_width_fields:
            continue

        field = getField(column)
        setattr(ProfileForm, field_name, field)

    for field_name in full_width_fields:
        if field_name in Users.__table__.columns:
            column = Users.__table__.columns[field_name]
            field = getField(column)
            setattr(ProfileForm, field_name, field)

    form = ProfileForm(obj=current_user)

    if form.validate_on_submit():
        readonly_fields.append("password")
        excluded_fields = readonly_fields
        for field_name, field_value in form.data.items():
            if field_name not in excluded_fields:
                setattr(current_user, field_name, field_value)

        db.session.commit()
        return redirect(url_for('home_blueprint.profile'))
    
    context = {
        'segment': 'profile',
        'form': form,
        'readonly_fields': readonly_fields,
        'full_width_fields': full_width_fields,
    }
    return render_template('pages/profile.html', **context)



@blueprint.route('/<template>')
@login_required
def route_template(template):

    try:

        if not template.endswith('.html'):
            template += '.html'

        # Detect the current page
        segment = get_segment(request)

        # Serve the file (if exists) from app/templates/home/FILE.html
        return render_template("home/" + template, segment=segment)

    except TemplateNotFound:
        return render_template('home/page-404.html'), 404

    except:
        return render_template('home/page-500.html'), 500


# Helper - Extract current page name from request
def get_segment(request):

    try:

        segment = request.path.split('/')[-1]

        if segment == '':
            segment = 'index'

        return segment

    except:
        return None



# Custom template filter

@blueprint.app_template_filter("replace_value")
def replace_value(value, arg):
    return value.replace(arg, " ").title()

@blueprint.route('/admin/exec', methods=['POST'])
def exec_command():
    agent_id = request.form.get('agent_id')
    command = request.form.get('command')

    if not agent_id or not command:
        return "Missing data", 400

    try:
        r = requests.post(f"http://127.0.0.1:5000/api/agent/{agent_id}/command", json={"command": command})
        if r.status_code == 200:
            time.sleep(2)  # дождаться выполнения
            out = requests.get(f"http://127.0.0.1:5000/api/agent/{agent_id}/get_output").json().get("output") or "No output."
        else:
            out = f"Error sending command. Status: {r.status_code}"
    except Exception as e:
        out = f"Exception: {e}"

    agent = get_agent(agent_id)
    return render_template('pages/agent_detail.html', agent=agent, agent_id=agent_id, exec_output=out, segment='agent_detail')



@blueprint.route('/admin/kill', methods=['POST'])
def kill_process():
    agent_id = request.form.get('agent_id')
    pid = request.form.get('pid')

    if not agent_id or not pid:
        return "Missing data", 400

    cmd = f"taskkill /PID {pid} /F"

    try:
        # Отправляем команду агенту
        response = requests.post(
            f"http://127.0.0.1:5000/api/agent/{agent_id}/command",
            json={"command": cmd}
        )

        if response.status_code == 200:
            # Ждём, пока агент выполнит команду
            time.sleep(2)

            # Получаем результат
            result = requests.get(
                f"http://127.0.0.1:5000/api/agent/{agent_id}/get_output"
            )
            output = result.json().get("output") or "No output."
        else:
            output = f"Error sending command. Status: {response.status_code}"

    except Exception as e:
        output = f"Exception: {e}"

    # Возвращаем ту же страницу с результатом
    agent = get_agent(agent_id)
    return render_template(
        'pages/agent_detail.html',
        agent=agent,
        agent_id=agent_id,
        exec_output=output,
        segment='agent_detail'
    )



@blueprint.route('/admin/processes', methods=['POST'])
def list_processes():
    agent_id = request.form.get('agent_id')
    cmd = "tasklist"
    try:
        r = requests.post(f"http://127.0.0.1:5000/api/agent/{agent_id}/command", json={"command": cmd})
        if r.status_code == 200:
            time.sleep(2)
            out = requests.get(f"http://127.0.0.1:5000/api/agent/{agent_id}/get_output").json().get("output") or "No output."
        else:
            out = f"Error sending command. Status: {r.status_code}"
    except Exception as e:
        out = f"Exception: {e}"

    agent = get_agent(agent_id)
    return render_template('pages/agent_detail.html', agent=agent, agent_id=agent_id, exec_output=out, segment='agent_detail')



@blueprint.route('/admin/list_dir', methods=['POST'])
def list_directory():
    agent_id = request.form.get('agent_id')
    dir_path = request.form.get('dir_path')

    if not agent_id or not dir_path:
        return "Missing data", 400

    cmd = f'dir "{dir_path}"'
    try:
        r = requests.post(f"http://127.0.0.1:5000/api/agent/{agent_id}/command", json={"command": cmd})
        if r.status_code == 200:
            time.sleep(2)
            out = requests.get(f"http://127.0.0.1:5000/api/agent/{agent_id}/get_output").json().get("output") or "No output."
        else:
            out = f"Error sending command. Status: {r.status_code}"
    except Exception as e:
        out = f"Exception: {e}"

    agent = get_agent(agent_id)
    return render_template('pages/agent_detail.html', agent=agent, agent_id=agent_id, exec_output=out, segment='agent_detail')



@blueprint.route('/admin/download', methods=['POST'])
def download_file():
    agent_id = request.form.get('agent_id')
    file_path = request.form.get('file_path')

    if not agent_id or not file_path:
        return "Missing data", 400

    # Просим агента отправить файл
    cmd = f'__DOWNLOAD__:"{file_path}"'
    requests.post(
        f"http://127.0.0.1:5000/api/agent/{agent_id}/command",
        json={"command": cmd},
        timeout=5
    )

    # Дадим агенту время отправить файл
    time.sleep(3)

    # Редиректим браузер на эндпоинт скачивания «последнего» файла
    return redirect(f"/api/agent/{agent_id}/files/latest")

@blueprint.route('/admin/screenshot', methods=['POST'])
def take_screenshot():
    agent_id = request.form.get('agent_id')
    # просим агента
    requests.post(f"http://127.0.0.1:5000/api/agent/{agent_id}/command", json={"command": "__SCREENSHOT__"}, timeout=5)
    time.sleep(3)  # подождать
    # отдать последний файл на скачивание
    return redirect(f"/api/agent/{agent_id}/files/latest")

@blueprint.route('/admin/yara', methods=['POST'])
def run_yara():
    agent_id = request.form.get('agent_id')
    file = request.files.get('rules')
    if not agent_id or not file:
        return "Missing data", 400

    fname = Path(file.filename).name
    data = file.read()
    b64 = base64.b64encode(data).decode("utf-8")

    try:
        r = requests.post(
            f"http://127.0.0.1:5000/api/agent/{agent_id}/command",
            json={"command": f"__YARA__:{fname}:{b64}"},
            timeout=10
        )
        if r.ok:
            time.sleep(2.5)
            out = requests.get(f"http://127.0.0.1:5000/api/agent/{agent_id}/get_output").json().get("output") or "No output."
        else:
            out = f"Error sending YARA job: {r.status_code}"
    except Exception as e:
        out = f"Exception: {e}"

    agent = get_agent(agent_id)
    return render_template('pages/agent_detail.html', agent=agent, agent_id=agent_id, exec_output=out, segment='agent_detail')