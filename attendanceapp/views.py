from django.shortcuts import render, redirect
import cv2
import os
import numpy as np
import pymysql
import face_recognition
import pickle
import tensorflow as tf
from tensorflow.python.keras.backend import set_session
from tensorflow.keras.models import load_model
from datetime import date, datetime, time as dtime, timedelta

global username
global person_login_id
global detector
global spoof_model
global graph
global session
global encoding_path
global attendance_marked

username = ''
person_login_id = ''
attendance_marked = False
encoding_path = "media/encodings"
DB_NAME = 'faceattendance'

# ================= ATTENDANCE TIME WINDOWS =================
# (start_time, end_time, session_type, status)
# Order matters: checked top to bottom, first match wins.
ATTENDANCE_WINDOWS = [
    (dtime(9, 0),   dtime(9, 30),  'Morning', 'Present'),
    (dtime(9, 30),  dtime(9, 45),  'Morning', 'Late'),
    (dtime(16, 0),  dtime(16, 15), 'Evening', 'Present'),
    (dtime(16, 15), dtime(16, 30), 'Evening', 'Late'),
]

def get_current_attendance_slot():
    """
    Returns (session_type, status) if the current time falls inside one of the
    defined attendance windows, otherwise returns None (meaning attendance
    cannot be marked right now).
    """
    now_time = datetime.now().time()
    for start, end, session_type, status in ATTENDANCE_WINDOWS:
        if start <= now_time < end:
            return (session_type, status)
    return None

def mark_absentees_for_past_dates(db_connection):
    """
    Safety-net reconciliation: for every date strictly before today, for every
    registered user, for every session (Morning/Evening), if there is no
    attendance row at all for that (user, date, session) combination, insert
    an 'Absent' row. This makes "missed attendance" show up automatically
    without needing a background scheduler running 24/7.

    Safe to call repeatedly (idempotent) - only inserts rows that don't exist.
    Only backfills from the user's registration date onwards, and only up to
    yesterday (today is still in progress and handled live).
    """
    cursor = db_connection.cursor()

    cursor.execute("SELECT person_id FROM users")
    all_users = [row[0] for row in cursor.fetchall()]
    if not all_users:
        return

    # attendance_date is stored as text 'dd-mm-yyyy', so we can't MIN() it
    # reliably in SQL. Fall back to a fixed lookback window instead.
    LOOKBACK_DAYS = 60
    today = date.today()

    for days_ago in range(1, LOOKBACK_DAYS + 1):
        check_date = today - timedelta(days=days_ago)
        date_str = check_date.strftime('%d-%m-%Y')

        # one query per date: fetch every (person_id, session_type) that
        # already has a row for this date, then only insert what's missing
        cursor.execute(
            "SELECT person_id, session_type FROM attendance WHERE attendance_date=%s",
            (date_str,)
        )
        existing_pairs = set(cursor.fetchall())

        rows_to_insert = []
        for person_id in all_users:
            for session_type in ('Morning', 'Evening'):
                if (person_id, session_type) not in existing_pairs:
                    rows_to_insert.append((person_id, date_str, '', 'Absent', session_type))

        if rows_to_insert:
            cursor.executemany(
                """
                INSERT INTO attendance
                (person_id, attendance_date, attendance_time, status, session_type)
                VALUES(%s,%s,%s,%s,%s)
                """,
                rows_to_insert
            )
    db_connection.commit()

TOTAL_DAYS = 120   # fixed denominator — 120 working days = 100%

def get_attendance_summary(db_connection, person_id):
    """
    Day-based summary. Each calendar day has a Morning and Evening session.
    Scoring per day:
      - Both sessions Present/Late  → Full Day  (1.0 point)
      - One Present/Late + one Absent → Half Day (0.5 point)
      - Both Absent                  → Absent    (0.0 point)
    Percentage = (points scored / TOTAL_DAYS) * 100
    """
    cursor = db_connection.cursor()
    cursor.execute(
        """
        SELECT attendance_date, session_type, status
        FROM attendance
        WHERE person_id=%s
        """,
        (person_id,)
    )
    rows = cursor.fetchall()

    # group by date → {date: {Morning: status, Evening: status}}
    from collections import defaultdict
    by_date = defaultdict(dict)
    for date_str, session_type, status in rows:
        by_date[date_str][session_type] = status

    full_days = 0
    half_days = 0
    absent_days = 0

    for date_str, sessions in by_date.items():
        morning = sessions.get('Morning', 'Absent')
        evening = sessions.get('Evening', 'Absent')
        morning_present = morning in ('Present', 'Late')
        evening_present = evening in ('Present', 'Late')

        if morning_present and evening_present:
            full_days += 1
        elif morning_present or evening_present:
            half_days += 1
        else:
            absent_days += 1

    # score: full=1, half=0.5, absent=0
    score = full_days + (half_days * 0.5)
    percentage = round((score / TOTAL_DAYS) * 100, 2)

    return {
        'full_days':   full_days,
        'half_days':   half_days,
        'absent_days': absent_days,
        'total_days':  TOTAL_DAYS,
        'score':       score,
        'percentage':  percentage,
    }

protoPath = "models/deploy.prototxt"
modelPath = "models/res10_300x300_ssd_iter_140000.caffemodel"
detector = cv2.dnn.readNetFromCaffe(protoPath,modelPath)

graph = tf.get_default_graph()
session = tf.Session()
set_session(session)
with graph.as_default():
    set_session(session)
    spoof_model = load_model("models/spoof_model.h5")

def index(request):
    return render(request, 'htmls/index.html')

# ================= LOGIN PAGE =================
def login(request):
    return render(request, 'htmls/login.html')

# ================= ADMIN LOGIN PAGE =================
def adminlogin(request):
    return render(request, 'htmls/adminlogin.html')

def adminloginAction(request):
    global username

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        if username == 'admin' and password == 'admin':
            return redirect('/adminhome.html')
        else:
            context = {'data':'Invalid Admin Login'}
            return render(request,'htmls/adminlogin.html',context)

    return render(request, 'htmls/adminlogin.html')

# ================= USER LOGIN PAGE =================
def userlogin(request):
    return render(request, 'htmls/userlogin.html')

def userloginAction(request):
    global person_login_id

    if request.method == 'POST':
        person_login_id = request.POST.get('personid')
        con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database=DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE person_id=%s",(person_login_id,))
        data = cur.fetchone()
        con.close()
        if data is not None:
            return redirect('/attendance.html')
        else:
            context = {'data':'Invalid ID'}
            return render(request,'htmls/userlogin.html',context)

    return render(request, 'htmls/userlogin.html')

# ================= LOGOUT =================
def logout(request):
    global username, person_login_id

    username = ''
    person_login_id = ''
    return redirect('/login.html')

# ================= ADMIN HOME =================
def adminhome(request):
    global username

    if username != 'admin':
        return redirect('/adminlogin.html')
    return render(request, 'htmls/adminhome.html')

# ================= REGISTER PAGE =================
def register(request):
    global username

    if username != 'admin':
        return redirect('/adminlogin.html')
    return render(request, 'htmls/register.html')

def registerAction(request):
    global username, encoding_path

    if username != 'admin':
        return redirect('/adminlogin.html')
    
    if request.method == 'POST':
        personid = request.POST.get('personid')
        personname = request.POST.get('personname')
        con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database=DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE person_id=%s",(personid,))
        data = cur.fetchone()
        encoding_file = (encoding_path + "/" + str(personid) +".pkl")
        if data is not None or os.path.exists(encoding_file):
            con.close()
            context = {'data':'User Already Registered'}
            return render(request,'htmls/register.html',context)

        query = """
        INSERT INTO users
        VALUES(%s,%s)
        """

        values = (personid,personname)
        cur.execute(query, values)
        con.commit()
        con.close()
        cam = cv2.VideoCapture(0)
        captured_encodings = []
        REQUIRED_SAMPLES = 5   # multiple encodings per user -> far more robust matching
        last_capture_time = 0
        CAPTURE_GAP = 0.6      # seconds between captures, so samples differ slightly (pose/lighting)

        while True:
            ret, frame = cam.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb)

            if len(face_locations) == 1:
                face_encodings = (face_recognition.face_encodings(rgb,face_locations))

                for face_encoding, face_location in zip(face_encodings,face_locations):
                    top, right, bottom, left = face_location
                    now = cv2.getTickCount() / cv2.getTickFrequency()

                    if (now - last_capture_time) >= CAPTURE_GAP and len(captured_encodings) < REQUIRED_SAMPLES:
                        captured_encodings.append(face_encoding)
                        last_capture_time = now

                    cv2.rectangle(frame,(left, top),(right, bottom),(0,255,0),2)
                    cv2.putText(
                        frame,
                        "Captured {}/{} - move head slightly".format(len(captured_encodings), REQUIRED_SAMPLES),
                        (left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,0),2
                    )

            cv2.imshow('Register Face',frame)

            if len(captured_encodings) >= REQUIRED_SAMPLES:
                with open(encoding_file,'wb') as file:
                    pickle.dump(captured_encodings,file)   # save LIST of encodings, not a single one
                cv2.waitKey(800)
                break

            if cv2.waitKey(1) == ord('q'):
                break

        cam.release()
        cv2.destroyAllWindows()

        if len(captured_encodings) < REQUIRED_SAMPLES:
            # roll back the DB row so the person isn't stuck "registered" with no usable face data
            rollback_con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database=DB_NAME)
            rollback_cur = rollback_con.cursor()
            rollback_cur.execute("DELETE FROM users WHERE person_id=%s",(personid,))
            rollback_con.commit()
            rollback_con.close()
            context = {'data':'Registration Cancelled - Not Enough Samples Captured. Please Try Again'}
            return render(request,'htmls/register.html',context)

        context = {'data':'User Registered Successfully'}
        return render(request,'htmls/register.html',context)
    
    return render(request, 'htmls/register.html')

# ================= ATTENDANCE PAGE =================
def attendance(request):
    global person_login_id

    if person_login_id == '':
        return redirect('/userlogin.html')

    slot = get_current_attendance_slot()
    if slot is None:
        context = {'window_closed': True}
    else:
        session_type, status = slot
        context = {'window_open_session': session_type}
    return render(request, 'htmls/attendance.html', context)

def attendanceAction(request):
    global person_login_id, attendance_marked
    global graph, session
    global spoof_model, encoding_path

    if person_login_id == '':
        return redirect('/userlogin.html')

    if request.method == 'POST':
        slot = get_current_attendance_slot()
        if slot is None:
            context = {
                'data': 'Attendance Window Closed. Allowed times: 9:00-9:45 AM (Morning), 4:00-4:30 PM (Evening)'
            }
            return render(request,'htmls/attendance.html',context)

        current_session_type, current_status = slot

        if not os.path.exists(encoding_path):
            context = {'data':'No Registered Users Available'}
            return render(request,'htmls/attendance.html',context)

        files = os.listdir(encoding_path)
        if len(files) == 0:
            context = {'data':'No Registered Users Available'}
            return render(request,'htmls/attendance.html',context)

        known_encodings = []   # each element is a LIST of encodings for one person
        known_ids = []

        for file in files:
            filepath = encoding_path + "/" + file
            with open(filepath, 'rb') as f:
                encoding = pickle.load(f)

            # backward-compatible: old files store a single encoding (1D array),
            # new files store a list of encodings (from multi-sample registration)
            if isinstance(encoding, list):
                person_encodings = encoding
            else:
                person_encodings = [encoding]

            person_id = file.split('.')[0]
            known_encodings.append(person_encodings)
            known_ids.append(person_id)

        cam = cv2.VideoCapture(0)
        attendance_marked = False
        while True:
            ret, frame = cam.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb)
            if len(face_locations) == 1:
                face_encodings = (face_recognition.face_encodings(rgb,face_locations))
                for face_encoding, face_location in zip(face_encodings,face_locations):
                    top, right, bottom, left = face_location
                    face = frame[top:bottom, left:right]
                    if face.size == 0:
                        continue

                    spoof_face = cv2.resize(face,(64,64))
                    ycrcb = cv2.cvtColor(spoof_face,cv2.COLOR_BGR2YCrCb)
                    luv = cv2.cvtColor(spoof_face,cv2.COLOR_BGR2LUV)
                    hist_y = cv2.calcHist([ycrcb],[0],None,[256],[0,256])
                    hist_l = cv2.calcHist([luv],[0],None,[256],[0,256])

                    spoof_face = (spoof_face.astype("float32") / 255.0)
                    spoof_face = np.expand_dims(spoof_face,axis=0)
                    with graph.as_default():
                        set_session(session)
                        prediction = (spoof_model.predict(spoof_face)[0])
                    confidence = prediction[1]

                    if confidence < 0.45:      # FAKE FACE
                        cv2.rectangle(frame,(left, top),(right, bottom),(0,0,255))
                        cv2.putText(frame,"Fake Face",(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,0,255),2)

                    else:       # REAL FACE
                        MATCH_TOLERANCE = 0.40   # stricter than before (was 0.45)
                        MIN_MARGIN = 0.06        # best match must be clearly better than 2nd best

                        # for each registered person, take their BEST (closest) distance
                        # across all of that person's stored sample encodings
                        per_person_best_distance = []
                        for person_encodings in known_encodings:
                            distances = face_recognition.face_distance(person_encodings, face_encoding)
                            per_person_best_distance.append(np.min(distances))

                        per_person_best_distance = np.array(per_person_best_distance)
                        sorted_idx = np.argsort(per_person_best_distance)
                        best_idx = sorted_idx[0]
                        best_distance = per_person_best_distance[best_idx]

                        is_confident_match = best_distance <= MATCH_TOLERANCE
                        if is_confident_match and len(sorted_idx) > 1:
                            second_best_distance = per_person_best_distance[sorted_idx[1]]
                            # if the top-2 matches are too close to each other, it's ambiguous
                            # (this is what prevents matching to a similar-looking friend)
                            if (second_best_distance - best_distance) < MIN_MARGIN:
                                is_confident_match = False

                        if is_confident_match:
                            matched_id = known_ids[best_idx]

                            if str(matched_id) == str(person_login_id):  # CORRECT USER
                                connection = pymysql.connect(host='localhost',user='root',password='root',database=DB_NAME,port=3306)
                                cursor = connection.cursor()
                                today_date = (date.today().strftime('%d-%m-%Y'))

                                # re-check the window right at insert time, in case the
                                # camera loop ran long and crossed into a new/closed window
                                live_slot = get_current_attendance_slot()
                                if live_slot is None:
                                    connection.close()
                                    cv2.rectangle(frame,(left, top),(right, bottom),(0,0,255),2)
                                    cv2.putText(frame,"Attendance Window Closed",(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,0,255),2)
                                else:
                                    live_session_type, live_status = live_slot

                                    cursor.execute(
                                        """
                                        SELECT status FROM attendance
                                        WHERE person_id=%s
                                        AND attendance_date=%s
                                        AND session_type=%s
                                        """,
                                        (matched_id,today_date,live_session_type)
                                    )

                                    data = cursor.fetchone()
                                    if data is None:
                                        current_time = (datetime.now().strftime('%H:%M:%S'))
                                        cursor.execute(
                                            """
                                            INSERT INTO attendance
                                            (
                                                person_id,
                                                attendance_date,
                                                attendance_time,
                                                status,
                                                session_type
                                            )
                                            VALUES(%s,%s,%s,%s,%s)
                                            """,
                                            (matched_id,today_date,current_time,live_status,live_session_type)
                                        )
                                        connection.commit()
                                        attendance_marked = True
                                        label = live_status + " (" + live_session_type + ")"
                                    elif data[0] == 'Absent':
                                        # backfill had marked this session absent (e.g. came right at
                                        # the edge of the window) - upgrade it to the real check-in
                                        current_time = (datetime.now().strftime('%H:%M:%S'))
                                        cursor.execute(
                                            """
                                            UPDATE attendance
                                            SET status=%s, attendance_time=%s
                                            WHERE person_id=%s AND attendance_date=%s AND session_type=%s
                                            """,
                                            (live_status,current_time,matched_id,today_date,live_session_type)
                                        )
                                        connection.commit()
                                        attendance_marked = True
                                        label = live_status + " (" + live_session_type + ")"
                                    else:
                                        # already marked for this session today
                                        attendance_marked = True
                                        label = "Already Marked (" + live_session_type + ")"

                                    connection.close()

                                    cv2.rectangle(frame,(left, top),(right, bottom),(0,255,0),2)
                                    cv2.putText(frame,"ID : " + str(matched_id),(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,255,0),2)
                                    cv2.putText(frame,label,(left, bottom + 25),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,0),2)

                            else:
                                cv2.rectangle(frame,(left, top),(right, bottom),(0,0,255),2)
                                cv2.putText(frame,"Unknown or Spoof Face",(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,0,255),2)

                        else:
                            cv2.rectangle(frame,(left, top),(right, bottom),(0,0,255),2)
                            cv2.putText(frame,"Unknown or Spoof Face",(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,0,255),2)

            cv2.imshow('Attendance System',frame)
            if cv2.waitKey(1) == ord('q'):
                break

        cam.release()
        cv2.destroyAllWindows()
        if attendance_marked:
            context = {'data':'Attendance Marked Successfully'}
        else:
            context = {'data':'Attendance Closed'}

        return render(request,'htmls/attendance.html',context)
    
    return render(request, 'htmls/attendance.html')

# ================= MY ATTENDANCE PAGE (USER) =================
def myattendance(request):
    global person_login_id

    if person_login_id == '':
        return redirect('/userlogin.html')

    con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database=DB_NAME)
    mark_absentees_for_past_dates(con)

    cur = con.cursor()
    cur.execute(
        """
        SELECT attendance_date, session_type, attendance_time, status
        FROM attendance
        WHERE person_id=%s
        ORDER BY STR_TO_DATE(attendance_date, '%%d-%%m-%%Y') DESC
        """,
        (person_login_id,)
    )
    rows = cur.fetchall()
    summary = get_attendance_summary(con, person_login_id)
    con.close()

    # merge into one row per day: {date: {Morning:{time,status}, Evening:{time,status}}}
    from collections import defaultdict, OrderedDict
    by_date = OrderedDict()
    for date_str, session_type, att_time, status in rows:
        if date_str not in by_date:
            by_date[date_str] = {'Morning': ('', 'Absent'), 'Evening': ('', 'Absent')}
        by_date[date_str][session_type] = (att_time, status)

    # build a list of day rows for the template
    day_rows = []
    for date_str, sessions in by_date.items():
        m_time, m_status = sessions['Morning']
        e_time, e_status = sessions['Evening']
        m_present = m_status in ('Present', 'Late')
        e_present = e_status in ('Present', 'Late')
        if m_present and e_present:
            day_status = 'Full Day'
        elif m_present or e_present:
            day_status = 'Half Day'
        else:
            day_status = 'Absent'
        day_rows.append({
            'date': date_str,
            'm_time': m_time, 'm_status': m_status,
            'e_time': e_time, 'e_status': e_status,
            'day_status': day_status,
        })

    context = {'day_rows': day_rows, 'summary': summary, 'personid': person_login_id}
    return render(request, 'htmls/myattendance.html', context)

# ================= VIEW ATTENDANCE PAGE =================
def viewattendance(request):
    global username

    if username != 'admin':
        return redirect('/adminlogin.html')

    return render(request,'htmls/viewattendance.html')

def viewattendanceAction(request):
    global username

    if username != 'admin':
        return redirect('/adminlogin.html')

    con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database=DB_NAME)
    mark_absentees_for_past_dates(con)

    cur = con.cursor()
    day_rows = []
    summary = None

    if request.method == 'POST':
        personid = request.POST.get('personid')
        cur.execute(
            """
            SELECT attendance_date, session_type, attendance_time, status
            FROM attendance
            WHERE person_id=%s
            ORDER BY STR_TO_DATE(attendance_date, '%%d-%%m-%%Y') DESC
            """,
            (personid,)
        )
        rows = cur.fetchall()
        summary = get_attendance_summary(con, personid)

        from collections import OrderedDict
        by_date = OrderedDict()
        for date_str, session_type, att_time, status in rows:
            if date_str not in by_date:
                by_date[date_str] = {'Morning': ('', 'Absent'), 'Evening': ('', 'Absent')}
            by_date[date_str][session_type] = (att_time, status)

        for date_str, sessions in by_date.items():
            m_time, m_status = sessions['Morning']
            e_time, e_status = sessions['Evening']
            m_present = m_status in ('Present', 'Late')
            e_present = e_status in ('Present', 'Late')
            if m_present and e_present:
                day_status = 'Full Day'
            elif m_present or e_present:
                day_status = 'Half Day'
            else:
                day_status = 'Absent'
            day_rows.append({
                'date': date_str,
                'm_time': m_time, 'm_status': m_status,
                'e_time': e_time, 'e_status': e_status,
                'day_status': day_status,
            })

    con.close()
    context = {'day_rows': day_rows, 'summary': summary}
    return render(request,'htmls/viewattendance.html',context)

# ================= DELETE USER PAGE =================
def deleteuser(request):
    global username

    if username != 'admin':
        return redirect('/adminlogin.html')
    return render(request,'htmls/deleteuser.html')

def deleteuserAction(request):
    global username, encoding_path

    if username != 'admin':
        return redirect('/adminlogin.html')

    if request.method == 'POST':
        personid = request.POST.get('personid')
        connection = pymysql.connect(host='localhost',user='root',password='root',database=DB_NAME,port=3306)
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM users WHERE person_id=%s",(personid,))
        user_data = cursor.fetchone()
        cursor.execute("SELECT * FROM attendance WHERE person_id=%s",(personid,))
        attendance_data = cursor.fetchone()
        encoding_file = (encoding_path + "/" +str(personid) +".pkl")
        encoding_exists = os.path.exists(encoding_file)

        if (user_data is None and attendance_data is None and not encoding_exists):
            connection.close()
            context = {'data':'ID Does Not Exist'}
            return render(request,'htmls/deleteuser.html',context)
        cursor.execute("DELETE FROM users WHERE person_id=%s",(personid,))
        cursor.execute("DELETE FROM attendance WHERE person_id=%s",(personid,))
        connection.commit()
        connection.close()
        if encoding_exists:
            os.remove(encoding_file)
        context = {'data':'User Deleted Successfully'}
        return render(request,'htmls/deleteuser.html',context)
    return render(request,'htmls/deleteuser.html')