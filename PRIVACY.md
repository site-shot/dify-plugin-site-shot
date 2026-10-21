# Privacy

The Site-Shot plugin for Dify sends data to one service only: the Site-Shot API at `https://api.site-shot.com/`, operated by Site-Shot.

## What is sent

- **Key check**, when the API key is saved in Dify: the key, in the `userkey` header of `GET /v1.0/credential-check`. Nothing else.
- **Each capture**: the API key (as the `userkey` query parameter, which the Site-Shot API requires), the page URL, and the format, width, height, full-page and max-height settings.
- **Both**: the user agent `site-shot-dify-plugin`. Site-Shot also sees the IP address of the server that runs the plugin.

## What Site-Shot does with it

Site-Shot loads the page in its own browser, renders the screenshot, returns it and counts the capture toward the account's plan. Site-Shot's privacy policy is at <https://www.site-shot.com/privacy-policy/>. This document makes no statement about how long Site-Shot keeps API request data such as page URLs.

## What the plugin and Dify keep

The plugin stores nothing and logs nothing about requests: not the API key, request URLs or response content. It passes the screenshot to Dify as the tool's file output. Dify stores the API key as a secret provider credential and stores each returned screenshot as a tool file, like other tool output; retention follows the Dify instance's settings.

## Personal data

The plugin asks for no personal data. A screenshot shows whatever the captured page shows, so if a page displays personal data, the screenshot contains it too.
