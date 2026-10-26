# Privacy

The plugin itself calls only the Site-Shot API at `https://api.site-shot.com/`. Site-Shot then loads the user-selected public page and its resources in a browser, which causes requests to the target website and any resource providers it uses.

## What is sent

- **Key check**, when the API key is saved in Dify: the key, in the `userkey` header of `GET /v1.0/credential-check`. No page URL or capture settings are sent.
- **Each capture**: the API key (as the `userkey` query parameter, which the Site-Shot API requires), the page URL, and the format, width, height, full-page and max-height settings.
- **Both**: the user agent `site-shot-dify-plugin`. Site-Shot also sees the IP address of the server that runs the plugin.

## What Site-Shot does with it

Site-Shot loads the selected page in its own browser, renders the screenshot, returns it and counts the capture toward the account's plan. The target website and its resource providers may receive requests from that browser, including the requested URLs and its network address, under their own policies. Site-Shot's [privacy policy](https://www.site-shot.com/privacy-policy/) describes its collection, use, sharing and general retention practices. It does not give a specific retention period for API-request page URLs or these returned screenshots, so this plugin policy does not promise one.

## What the plugin and Dify keep

The plugin does not persist requests or screenshots or intentionally log the API key, request URLs or response content. It processes the request and image in memory, then passes the screenshot to Dify as the tool's file output. Dify stores the API key as a secret provider credential and may retain the returned file and workflow records under that instance's settings; this document does not promise a Dify retention period.

## Personal data

The tool does not require a name or email address as an input. However, the account key, the Dify server IP address, a selected URL (including its path or query), and the returned image can contain or be associated with personal data. Screenshots may also show sensitive content from the page. Use only pages you are authorized to capture and avoid including personal data in URLs unless needed for your workflow. For privacy questions or requests about Site-Shot data, use the contact in the [Site-Shot privacy policy](https://www.site-shot.com/privacy-policy/).
