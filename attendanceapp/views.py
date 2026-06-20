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
from datetime import date, datetime

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
        con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database='attendance')
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
        con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database='attendance')
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
        encoding_saved = False

        while True:
            ret, frame = cam.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb)

            if len(face_locations) == 1:
                face_encodings = (face_recognition.face_encodings(rgb,face_locations))

                for face_encoding, face_location in zip(face_encodings,face_locations):
                    with open(encoding_file,'wb') as file:
                        pickle.dump(face_encoding,file)

                    encoding_saved = True
                    top, right, bottom, left = face_location
                    cv2.rectangle(frame,(left, top),(right, bottom),(0,255,0),2)
                    cv2.putText(frame,"Face Captured",(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,255,0),2)

            cv2.imshow('Register Face',frame)
            if encoding_saved:
                cv2.waitKey(2000)
                break

            if cv2.waitKey(1) == ord('q'):
                break

        cam.release()
        cv2.destroyAllWindows()
        context = {'data':'User Registered Successfully'}
        return render(request,'htmls/register.html',context)
    
    return render(request, 'htmls/register.html')

# ================= ATTENDANCE PAGE =================
def attendance(request):
    global person_login_id

    if person_login_id == '':
        return redirect('/userlogin.html')
    return render(request, 'htmls/attendance.html')

def attendanceAction(request):
    global person_login_id, attendance_marked
    global graph, session
    global spoof_model, encoding_path

    if person_login_id == '':
        return redirect('/userlogin.html')

    if request.method == 'POST':
        if not os.path.exists(encoding_path):
            context = {'data':'No Registered Users Available'}
            return render(request,'htmls/attendance.html',context)

        files = os.listdir(encoding_path)
        if len(files) == 0:
            context = {'data':'No Registered Users Available'}
            return render(request,'htmls/attendance.html',context)

        known_encodings = []
        known_ids = []

        for file in files:
            filepath = encoding_path + "/" + file
            with open(filepath, 'rb') as f:
                encoding = pickle.load(f)

            person_id = file.split('.')[0]
            known_encodings.append(encoding)
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
                        matches = (face_recognition.compare_faces(known_encodings,face_encoding,tolerance=0.45))
                        face_distances = (face_recognition.face_distance(known_encodings,face_encoding))
                        best_match_index = np.argmin(face_distances)
                        if matches[best_match_index]:
                            matched_id = (known_ids[best_match_index])

                            if str(matched_id) == str(person_login_id):  # CORRECT USER
                                connection = pymysql.connect(host='localhost',user='root',password='root',database='attendance',port=3306)
                                cursor = connection.cursor()
                                today_date = (date.today().strftime('%d-%m-%Y'))

                                cursor.execute(
                                    """
                                    SELECT * FROM attendance
                                    WHERE person_id=%s
                                    AND attendance_date=%s
                                    """,
                                    (matched_id,today_date)
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
                                            status
                                        )
                                        VALUES(%s,%s,%s,%s)
                                        """,
                                        (matched_id,today_date,current_time,'Present')
                                    )
                                    connection.commit()
                                connection.close()

                                attendance_marked = True
                                cv2.rectangle(frame,(left, top),(right, bottom),(0,255,0),2)
                                cv2.putText(frame,"ID : " + str(matched_id),(left, top - 10),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,255,0),2)
                                cv2.putText(frame,"Attendance Marked",(left, bottom + 25),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,255,0),2)

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

    con = pymysql.connect(host='localhost',user='root',password='root',port=3306,database='attendance')
    cur = con.cursor()
    data = []
    if request.method == 'POST':
        personid = request.POST.get('personid')
        query = """
        SELECT * FROM attendance
        WHERE person_id=%s
        """
        cur.execute(query, (personid,))
        data = cur.fetchall()
    con.close()
    context = {'data':data}
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
        connection = pymysql.connect(host='localhost',user='root',password='root',database='attendance',port=3306)
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