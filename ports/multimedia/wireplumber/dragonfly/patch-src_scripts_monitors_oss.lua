--- /dev/null
+++ src/scripts/monitors/oss.lua
@@ -0,0 +1,66 @@
+-- WirePlumber
+--
+-- SPDX-License-Identifier: MIT
+
+local log = Log.open_topic("s-monitors-oss")
+oss_nodes = {}
+
+local function create_node(unit, description, stream, is_default)
+  local is_capture = stream == "capture"
+  local direction = is_capture and "input" or "output"
+  local priority = is_default and 1000 or 744 - (tonumber(unit) * 16)
+
+  if is_capture then
+    priority = priority + 1000
+  end
+  local properties = {
+    ["factory.name"] = is_capture and "api.alsa.pcm.source" or "api.alsa.pcm.sink",
+    ["node.name"] = "oss_" .. direction .. ".pcm" .. unit,
+    ["node.description"] = description .. " (pcm" .. unit .. ")",
+    ["node.nick"] = description,
+    ["media.class"] = is_capture and "Audio/Source" or "Audio/Sink",
+    ["api.alsa.path"] = (is_capture and "oss:DEVICE=/dev/dsp" or "dfly_ossplug:DEVICE=/dev/dsp") .. unit,
+    ["node.pause-on-idle"] = true,
+    ["priority.driver"] = priority,
+    ["priority.session"] = priority,
+  }
+  if not is_capture then
+    properties["api.alsa.disable-mmap"] = true
+  end
+  local layout, layout_err = DragonFly.get_pcm_layout(tonumber(unit), stream)
+
+  if layout then
+    properties["audio.channels"] = layout.channels
+    properties["audio.position"] = layout.position
+  elseif layout_err then
+    log:warning("cannot read channel layout for /dev/dsp" .. unit ..
+        ": " .. tostring(layout_err))
+  end
+
+  local node = Node("adapter", properties)
+  node:activate(Feature.Proxy.BOUND, function()
+    log:info("created " .. direction .. " node for /dev/dsp" .. unit)
+  end)
+  table.insert(oss_nodes, node)
+end
+
+local contents, err = DragonFly.read_sndstat()
+if not contents then
+  log:warning("cannot read /dev/sndstat: " .. tostring(err))
+  return
+end
+
+for line in contents:gmatch("[^\n]+") do
+  local unit, description, capabilities =
+      line:match("^pcm(%d+):%s*<(.*)>%s*%(([^)]*)%)")
+  if unit then
+    local is_default = line:find("%f[%a]default%f[%A]") ~= nil
+
+    if capabilities:find("play", 1, true) then
+      create_node(unit, description, "playback", is_default)
+    end
+    if capabilities:find("rec", 1, true) then
+      create_node(unit, description, "capture", is_default)
+    end
+  end
+end
