--- qtox.pro.old	2015-08-08 02:31:07.225406000 +0200
+++ qtox.pro	2015-08-08 02:32:27.034808000 +0200
@@ -493,6 +493,7 @@
     icon.path = $$PREFIX/share/pixmaps
 
     INSTALLS = target desktop icon
+    INCLUDEPATH += "/usr/local/include/ffmpeg26"
 }
 
 HEADERS += \
