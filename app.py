import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
import pymysql.cursors

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Динамический ключ сессии для безопасности


# --- КОНФИГУРАЦИЯ БАЗЫ ДАННЫХ ---
def get_db_connection():
    return pymysql.connect(
        host='localhost',
        user='root',
        password='Digrel4ik',  # Укажите ваш пароль
        database='uchpractica',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )


# --- ДЕКОРАТОРЫ ДОСТУПА ---
def login_required(f):
    """Проверка, залогинен ли пользователь"""

    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, авторизуйтесь в системе', 'danger')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)

    decorated_function.__name__ = f.__name__
    return decorated_function


def admin_only(f):
    """Доступ только для роли Администратор (id_role = 1)"""

    def decorated_function(*args, **kwargs):
        if session.get('role_id') != 1:
            flash('У вас недостаточно прав для этого действия', 'danger')
            return redirect(url_for('clients'))
        return f(*args, **kwargs)

    decorated_function.__name__ = f.__name__
    return decorated_function


# --- КОНТЕКСТНЫЕ ПЕРЕМЕННЫЕ (Доступны во всех шаблонах) ---
@app.context_processor
def inject_user():
    return dict(
        current_user_name=session.get('user_name'),
        current_role=session.get('role_id')
    )


# --- МАРШРУТЫ: АВТОРИЗАЦИЯ ---
@app.route('/')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('clients'))
    return render_template('login.html')


@app.route('/auth', methods=['POST'])
def auth():
    login = request.form.get('login')
    password = request.form.get('password')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT id_staff, full_name, id_role FROM staff WHERE login=%s AND password=%s"
            cursor.execute(sql, (login, password))
            user = cursor.fetchone()

            if user:
                session['user_id'] = user['id_staff']
                session['user_name'] = user['full_name']
                session['role_id'] = user['id_role']
                flash(f'Успешный вход! Добро пожаловать, {user["full_name"]}', 'success')
                return redirect(url_for('clients'))
            else:
                flash('Неверный логин или пароль', 'danger')
                return redirect(url_for('login_page'))
    finally:
        conn.close()


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# --- МАРШРУТЫ: КЛИЕНТЫ ---
@app.route('/clients')
@login_required
def clients():
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'newest')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Маскировка телефона для не-админов
            phone_sql = "c.phone" if session[
                                         'role_id'] == 1 else "CONCAT(LEFT(c.phone, 4), ' ***-**-', RIGHT(c.phone, 2))"

            base_query = f"""
                SELECT c.id_client, c.full_name, {phone_sql} as phone, c.birth_date,
                       st.title as sub_name, s.remaining_visits, s.status
                FROM clients c
                LEFT JOIN subscriptions s ON c.id_client = s.id_client AND s.status = 'active'
                LEFT JOIN subscription_types st ON s.id_sub_type = st.id_sub_type
                WHERE c.full_name LIKE %s OR c.phone LIKE %s
            """

            if sort == 'name_asc':
                base_query += " ORDER BY c.full_name ASC"
            elif sort == 'name_desc':
                base_query += " ORDER BY c.full_name DESC"
            else:
                base_query += " ORDER BY c.id_client DESC"

            cursor.execute(base_query, (f'%{search}%', f'%{search}%'))
            data = cursor.fetchall()
            return render_template('clients.html', clients=data)
    finally:
        conn.close()


@app.route('/client/add', methods=['POST'])
@login_required
def client_add():
    name = request.form.get('name')
    phone = request.form.get('phone')
    bday = request.form.get('bday')

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO clients (full_name, phone, birth_date) VALUES (%s, %s, %s)",
                       (name, phone, bday))
    flash(f'Клиент {name} успешно зарегистрирован', 'success')
    return redirect(url_for('clients'))


# --- МАРШРУТЫ: АБОНЕМЕНТЫ ---
@app.route('/subscriptions')
@login_required
def subscriptions():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Получаем все продажи с расчетом итоговой цены
            cursor.execute("""
                SELECT s.*, c.full_name, st.title, st.price,
                (st.price - (st.price * s.discount / 100)) as final_cost
                FROM subscriptions s
                JOIN clients c ON s.id_client = c.id_client
                JOIN subscription_types st ON s.id_sub_type = st.id_sub_type
                ORDER BY s.id_subscription DESC
            """)
            subs = cursor.fetchall()

            cursor.execute("SELECT id_client, full_name, phone FROM clients")
            clients_list = cursor.fetchall()

            cursor.execute("SELECT * FROM subscription_types")
            types = cursor.fetchall()

            return render_template('subscriptions.html', subs=subs, clients=clients_list, types=types)
    finally:
        conn.close()


@app.route('/sub/add', methods=['POST'])
@admin_only
def sub_add():
    c_id = request.form.get('client_id')
    t_id = request.form.get('type_id')
    discount = int(request.form.get('discount', 0))

    conn = get_db_connection()
    with conn.cursor() as cursor:
        # Получаем параметры типа абонемента
        cursor.execute("SELECT duration_days, visits_limit FROM subscription_types WHERE id_sub_type=%s", (t_id,))
        t_info = cursor.fetchone()

        start_date = datetime.now()
        end_date = start_date + timedelta(days=t_info['duration_days'])

        cursor.execute("""
            INSERT INTO subscriptions (id_client, id_sub_type, purchase_date, start_date, end_date, remaining_visits, discount, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
        """, (c_id, t_id, start_date, start_date, end_date, t_info['visits_limit'], discount))

    flash('Абонемент успешно оформлен и оплачен', 'success')
    return redirect(url_for('subscriptions'))


# --- МАРШРУТЫ: РАСПИСАНИЕ И ПОСЕЩЕНИЯ ---
@app.route('/schedule')
@login_required
def schedule():
    hall_id = request.args.get('hall')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Базовый запрос
            query = """
                SELECT sch.*, act.activity_name, h.hall_name, s.full_name as coach 
                FROM schedule sch
                JOIN activity_types act ON sch.id_activity_type = act.id_activity_type
                JOIN halls h ON sch.id_hall = h.id_hall
                JOIN staff s ON sch.id_staff = s.id_staff
                WHERE 1=1
            """
            params = []

            # ФИЛЬТР: Если залогинен ТРЕНЕР (role_id = 2)
            if session.get('role_id') == 2:
                query += " AND sch.id_staff = %s"
                params.append(session.get('user_id'))  # Используем ID из сессии

            # ФИЛЬТР: По залу (если выбран)
            if hall_id:
                query += " AND sch.id_hall = %s"
                params.append(hall_id)

            query += " ORDER BY sch.start_time ASC"

            cursor.execute(query, params)
            lessons = cursor.fetchall()

            # Получаем список залов для фильтра (видят все)
            cursor.execute("SELECT * FROM halls")
            halls = cursor.fetchall()

            # Список активных абонементов для отметки посещений
            cursor.execute("""
                SELECT s.id_subscription, c.full_name, s.remaining_visits 
                FROM subscriptions s 
                JOIN clients c ON s.id_client = c.id_client 
                WHERE s.status = 'active'
            """)
            active_subs = cursor.fetchall()

            return render_template('schedule.html',
                                   lessons=lessons,
                                   halls=halls,
                                   active_subs=active_subs)
    finally:
        conn.close()


@app.route('/visit/mark', methods=['POST'])
@login_required
def visit_mark():
    sub_id = request.form.get('sub_id')
    lesson_id = request.form.get('lesson_id')

    conn = get_db_connection()
    with conn.cursor() as cursor:
        # Проверяем остаток занятий
        cursor.execute("SELECT remaining_visits FROM subscriptions WHERE id_subscription=%s", (sub_id,))
        sub = cursor.fetchone()

        if sub['remaining_visits'] < 999:  # Если не безлимит
            if sub['remaining_visits'] <= 0:
                flash('Ошибка: занятия закончились!', 'danger')
                return redirect(url_for('schedule'))
            cursor.execute("UPDATE subscriptions SET remaining_visits = remaining_visits - 1 WHERE id_subscription=%s",
                           (sub_id,))

        # Записываем в журнал посещений
        cursor.execute("INSERT INTO attendance (id_lesson, id_subscription, visit_time) VALUES (%s, %s, NOW())",
                       (lesson_id, sub_id))

    flash('Посещение успешно отмечено в журнале', 'success')
    return redirect(url_for('schedule'))


# --- МАРШРУТЫ: ОТЧЕТЫ (KPI) ---
@app.route('/reports')
@admin_only
def reports():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Выручка
            cursor.execute("""
                SELECT SUM(st.price - (st.price * s.discount / 100)) as total
                FROM subscriptions s
                JOIN subscription_types st ON s.id_sub_type = st.id_sub_type
                WHERE MONTH(s.purchase_date) = MONTH(CURRENT_DATE())
            """)
            revenue = cursor.fetchone()['total'] or 0

            # 2. Активные клиенты (у кого абонемент действует сегодня)
            cursor.execute(
                "SELECT COUNT(DISTINCT id_client) as cnt FROM subscriptions WHERE status = 'active' AND end_date >= CURDATE()")
            active_count = cursor.fetchone()['cnt'] or 0

            # 3. Визиты СЕГОДНЯ
            cursor.execute("SELECT COUNT(*) as cnt FROM attendance WHERE DATE(visit_time) = CURDATE()")
            today_visits = cursor.fetchone()['cnt'] or 0

            # 4. Средний чек
            cursor.execute("""
                SELECT AVG(st.price - (st.price * s.discount / 100)) as avg_b 
                FROM subscriptions s 
                JOIN subscription_types st ON s.id_sub_type = st.id_sub_type
            """)
            avg_bill = cursor.fetchone()['avg_b'] or 0
            avg_bill = round(float(avg_bill), 2)  # Округляем до копеек

            # 5. Популярность направлений (с фиксом ONLY_FULL_GROUP_BY)
            cursor.execute("""
                SELECT act.activity_name, COUNT(att.id_attendance) as visits, ANY_VALUE(staff.full_name) as coach_name
                FROM attendance att
                JOIN schedule sch ON att.id_lesson = sch.id_lesson
                JOIN activity_types act ON sch.id_activity_type = act.id_activity_type
                JOIN staff ON sch.id_staff = staff.id_staff
                GROUP BY act.id_activity_type, act.activity_name
                ORDER BY visits DESC
            """)
            stats = cursor.fetchall()

            # 6. Доля тарифов
            cursor.execute("""
                SELECT st.title, COUNT(s.id_subscription) as count,
                (COUNT(s.id_subscription) * 100 / (SELECT COUNT(*) FROM subscriptions)) as percent
                FROM subscriptions s
                JOIN subscription_types st ON s.id_sub_type = st.id_sub_type
                GROUP BY st.id_sub_type, st.title
            """)
            sub_types_stats = cursor.fetchall()

            return render_template('reports.html',
                                   revenue=revenue,
                                   active_count=active_count,
                                   today_visits=today_visits,
                                   avg_bill=avg_bill,
                                   stats=stats,
                                   sub_types_stats=sub_types_stats)
    finally:
        conn.close()


# --- МАРШРУТЫ: НАСТРОЙКИ И ПЕРСОНАЛ ---
@app.route('/settings')
@admin_only
def settings():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM staff")
        staff = cursor.fetchall()
        cursor.execute("SELECT * FROM roles")
        roles = cursor.fetchall()
    return render_template('settings.html', staff=staff, roles=roles)


@app.route('/staff/save', methods=['POST'])
@admin_only
def staff_save():
    s_id = request.form.get('staff_id')
    name = request.form.get('name')
    login = request.form.get('login')
    role_id = request.form.get('role_id')
    new_pass = request.form.get('new_password')

    conn = get_db_connection()
    with conn.cursor() as cursor:
        # Базовое обновление
        if s_id == str(session['user_id']):
            # Себя нельзя разжаловать
            cursor.execute("UPDATE staff SET full_name=%s, login=%s WHERE id_staff=%s", (name, login, s_id))
        else:
            cursor.execute("UPDATE staff SET full_name=%s, login=%s, id_role=%s WHERE id_staff=%s",
                           (name, login, role_id, s_id))

        # Обновление пароля, если введен
        if new_pass:
            cursor.execute("UPDATE staff SET password=%s WHERE id_staff=%s", (new_pass, s_id))

    flash('Данные сотрудника обновлены', 'success')
    return redirect(url_for('settings'))


@app.route('/client/edit/<int:id>', methods=['POST'])
@login_required
@admin_only
def client_edit_save(id):
    name = request.form.get('name')
    phone = request.form.get('phone')
    bday = request.form.get('bday')

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Обновляем данные конкретного клиента по его ID
            sql = """
                UPDATE clients 
                SET full_name = %s, phone = %s, birth_date = %s 
                WHERE id_client = %s
            """
            cursor.execute(sql, (name, phone, bday, id))

        flash(f'Данные клиента {name} успешно обновлены', 'success')
    except Exception as e:
        flash(f'Ошибка при обновлении: {str(e)}', 'danger')
    finally:
        conn.close()

    return redirect(url_for('clients'))


@app.route('/sub/delete/<int:id>')
@admin_only
def sub_delete(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Сначала проверяем, есть ли по этому абонементу посещения
            cursor.execute("SELECT COUNT(*) as count FROM attendance WHERE id_subscription=%s", (id,))
            has_visits = cursor.fetchone()['count']

            if has_visits > 0:
                flash('Нельзя удалить абонемент, по которому уже были посещения! Сначала удалите записи в журнале.',
                      'danger')
            else:
                cursor.execute("DELETE FROM subscriptions WHERE id_subscription=%s", (id,))
                flash('Абонемент успешно аннулирован', 'success')
    except Exception as e:
        flash(f'Ошибка при удалении: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('subscriptions'))

@app.route('/client/delete/<int:id>')
@admin_only
def client_delete(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Проверяем наличие абонементов
            cursor.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE id_client=%s", (id,))
            if cursor.fetchone()['cnt'] > 0:
                flash('Нельзя удалить клиента: у него есть оформленные абонементы!', 'danger')
            else:
                cursor.execute("DELETE FROM clients WHERE id_client=%s", (id,))
                flash('Клиент успешно удален из базы', 'success')
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('clients'))

@app.route('/schedule/delete/<int:id>')
@admin_only
def schedule_delete(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Проверяем, не отметили ли уже кого-то на это занятие
            cursor.execute("SELECT COUNT(*) as cnt FROM attendance WHERE id_lesson=%s", (id,))
            if cursor.fetchone()['cnt'] > 0:
                flash('Нельзя отменить занятие: на него уже есть отметки посещений!', 'danger')
            else:
                cursor.execute("DELETE FROM schedule WHERE id_lesson=%s", (id,))
                flash('Тренировка убрана из расписания', 'success')
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('schedule'))

@app.route('/attendance')
@login_required
def attendance_log():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Получаем список всех посещений с именами клиентов и названиями тренировок
            sql = """
                SELECT 
                    a.id_attendance, 
                    a.visit_time, 
                    c.full_name as client_name, 
                    act.activity_name,
                    s.id_subscription
                FROM attendance a
                JOIN subscriptions s ON a.id_subscription = s.id_subscription
                JOIN clients c ON s.id_client = c.id_client
                JOIN schedule sch ON a.id_lesson = sch.id_lesson
                JOIN activity_types act ON sch.id_activity_type = act.id_activity_type
                ORDER BY a.visit_time DESC
            """
            cursor.execute(sql)
            logs = cursor.fetchall()
            return render_template('attendance.html', logs=logs)
    finally:
        conn.close()

@app.route('/attendance/delete/<int:id>')
@admin_only
def attendance_delete(id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM attendance WHERE id_attendance=%s", (id,))
        flash('Запись о посещении удалена. Занятие вернулось на баланс абонемента.', 'success')
    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('attendance_log'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)