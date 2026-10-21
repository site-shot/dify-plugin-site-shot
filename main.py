from dify_plugin import DifyPluginEnv, Plugin

# Above the 90-second capture deadline in site_shot_api.py.
plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=120))

if __name__ == "__main__":
    plugin.run()
