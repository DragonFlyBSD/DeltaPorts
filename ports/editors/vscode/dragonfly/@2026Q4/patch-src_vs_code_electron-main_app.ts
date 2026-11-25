--- src/vs/code/electron-main/app.ts.orig
+++ src/vs/code/electron-main/app.ts
@@ -1179,6 +1179,7 @@
 
 			case 'linux':
 			case 'freebsd':
+			case 'dragonfly':
 				if (isLinuxSnap) {
 					services.set(IUpdateService, new SyncDescriptor(SnapUpdateService, [process.env['SNAP'], process.env['SNAP_REVISION']]));
 				} else {
