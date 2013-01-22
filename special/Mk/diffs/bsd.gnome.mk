--- bsd.gnome.mk.orig	2012-11-28 18:28:41.000000000 +0100
+++ bsd.gnome.mk	2013-01-23 00:22:32.396810000 +0100
@@ -675,6 +675,11 @@
 _USE_GNOME+=	${${component}_USE_GNOME_IMPL} ${component}
 . endfor
 
+# Build on LDFLAGS when libintl is specified
+.if ${USE_GNOME:Mintltool} != "" || ${USE_GNOME:Mintlhack} != ""
+LDFLAGS+= -L${LOCALBASE}/lib -lintl
+.endif
+
 # Setup the GTK+ API version for pixbuf loaders, input method modules,
 # and theme engines.
 PLIST_SUB+=			GTK2_VERSION="${GTK2_VERSION}" \
