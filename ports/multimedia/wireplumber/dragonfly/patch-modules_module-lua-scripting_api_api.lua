--- modules/module-lua-scripting/api/api.lua.orig
+++ modules/module-lua-scripting/api/api.lua
@@ -190,9 +190,10 @@ WpConf["__new"] = WpConf_new
 SANDBOX_EXPORT = {
   Debug = Debug,
   Id = Id,
   Features = Features,
   Feature = Feature,
   GLib = GLib,
+  DragonFly = DragonFly,
   I18n = I18n,
   Log = WpLog,
   Core = WpCore,
