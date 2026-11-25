--- src/vs/platform/environment/node/userDataPath.ts.orig
+++ src/vs/platform/environment/node/userDataPath.ts
@@ -97,6 +97,7 @@
 			break;
 		case 'linux':
 		case 'freebsd':
+		case 'dragonfly':
 			appDataPath = process.env['XDG_CONFIG_HOME'] || join(homedir(), '.config');
 			break;
 		default:
