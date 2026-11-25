--- src/bootstrap-node.ts.orig
+++ src/bootstrap-node.ts
@@ -227,7 +227,7 @@
 			return process.env['VSCODE_PORTABLE'];
 		}
 
-		if (process.platform === 'win32' || process.platform === 'linux' || process.platform === 'freebsd') {
+		if (process.platform === 'win32' || process.platform === 'linux' || process.platform === 'freebsd' || process.platform === 'dragonfly') {
 			return path.join(getApplicationPath(), 'data');
 		}
 
