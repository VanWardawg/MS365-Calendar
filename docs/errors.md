---
title: Errors
nav_order: 19
---

# Errors
* **The reply URL specified in the request does not match the reply URLs configured for the application.**

  or
  
* **The provided value for the input parameter 'redirect_uri' is not valid.**
  * Please ensure that you have configured the internet URL on your Home Assistant network settings config and that you have added the correct redirect URI to your Entra ID App Registration as described in the [Prerequisites](./prerequisites.md)

* **Client is public so neither 'client_assertion' nor 'client_secret' should be presented.**
  * Please ensure that you have set "Allow public client flows" to Yes in your Entra ID App Registration under Authentication ![image](https://user-images.githubusercontent.com/36969394/198787952-9f818372-7684-42e1-ac30-a8ab05a5f478.png)
 
* **Application {x} is not configured as a multi-tenant application.**
  * In your Entra ID App Registration go to Manifest, find the key "signInAudience", change its value to "AzureADandPersonalMicrosoftAccount"

* **The logged in user is not authorized to fetch tokens for extension 'Microsoft_AAD_RegisteredApps' because the account is not a member of tenant 'xxxx'.**
  * Please make sure you have set the Supported accounts correctly as described in the [Prerequisites](./prerequisites.md)

* **No token, or token doesn't have all required permissions; requesting authorization for account: Account1 Minimum required permissions not granted: ['Presence.Read', []]**
  * Where this error mentions `Presence.Read` or `Chat.Read` it probably means you have tried to configure a Teams Presence or Chat sensor when your account is a Personal Account (such as @outlook.com)
  * For other items, it means you have changed your configuration to require new permissions. You will likely need to delete your token and reauthenticate. Please check the [token page](./token.md) for more details.

 * **The user could not be authenticated as the grant is expired. The user must sign in again.**
   * Create a new secret and reconfigure your MS365 configuration.

* **Client secret expired for account: xxxxxxxx. Create new client id in Entra ID App Registration.**
  * The Client Secret on your Entra ID App Registration has expired. Create a new secret and reconfigure your MS365 configuration.

* **Unable to fetch auth token. Error: invalid_client**
  * Ensure the configured secret is the client secret __value__, not the client secret ID, and that the secret has not expired

* **Token corrupt for account - please delete and re-authenticate.**
  * Reconfigure your MS365 configuration to authenticate again and generate a new token.

* **MS365 config requests permission: 'xxxxxx.xxxxxxx'. Not available in token 'ms365_xxxxxxxx.token' for account 'xxxxxxxx'**
Validate your Entra ID App Registration permissions match those required as detailed on the [permissions page](./permissions.md). If they are correct, Reconfigure your MS365 configuration to authenticate again and generate a new token.

**_Please note that any changes made to your Entra ID App Registration settings takes a few minutes to propagate. Please wait around 5 minutes between changes to your settings and any auth attempts from Home Assistant._**

# Installation issues
 * If your installation does not complete authentication, or the sensors are not created, please go back and ensure you have accurately followed the steps detailed, also look in the logs to see if there are any errors. 
