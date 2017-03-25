# ehem
--- compiler.pri.intermediate	2016-12-17 18:25:04.000000000 +0200
+++ compiler.pri
@@ -111,7 +111,7 @@ unix:!macx {
 	CONFIG(debug, debug|release) {
 		QMAKE_CFLAGS *= -fstack-protector -fPIE -pie
 		QMAKE_CXXFLAGS *= -fstack-protector -fPIE -pie
-		QMAKE_LFLAGS = -Wl
+		QMAKE_LFLAGS =
 	}
 
 	DEFINES *= _FORTIFY_SOURCE=2
