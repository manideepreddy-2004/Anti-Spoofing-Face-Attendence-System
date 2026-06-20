from django.urls import path
from . import views

urlpatterns = [
    path('index.html/',views.index,name='index'),
    path('login.html',views.login,name='login'),
    path('adminlogin.html',views.adminlogin,name='adminlogin'),
    path('adminloginAction',views.adminloginAction,name='adminloginAction'),
    path('userlogin.html',views.userlogin,name='userlogin'),
    path('userloginAction',views.userloginAction,name='userloginAction'),
    path('logout',views.logout,name='logout'),
    path('adminhome.html',views.adminhome,name='adminhome'),
    path('register.html',views.register,name='register'),
    path('registerAction',views.registerAction,name='registerAction'),
    path('attendance.html',views.attendance,name='attendance'),
    path('attendanceAction',views.attendanceAction,name='attendanceAction'),
    path('viewattendance.html',views.viewattendance,name='viewattendance'),
    path('viewattendanceAction',views.viewattendanceAction,name='viewattendanceAction'),
    path('deleteuser.html',views.deleteuser,name='deleteuser'),
    path('deleteuserAction',views.deleteuserAction,name='deleteuserAction'),
]