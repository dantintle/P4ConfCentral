Google App Engine + datastore for the Udacity Project 4 Conference App. Back end API creates user profile/allows user to manage wishlist sessions that interest them, add/edit/remove conferences, and add/edit/remove sessions/session speakers. Live app at https://scalable-project-1028.appspot.com/

## Products
- App Engine: https://developers.google.com/appengine

## Language
- Python: http://python.org

## APIs
- Google Cloud Endpoints: https://developers.google.com/appengine/docs/python/endpoints/

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
2. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the Developer Console: https://console.developers.google.com/ 
3. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
4. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
5. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default https://localhost:8080/)
6. (Optional) Generate your client library(ies) with the endpoints tool: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool.

##Deploy to App Engine
7. Click on Add Existing Application. Select directory where app is contained. Hit deploy.
8. Navigate to https://{{projectID}}.appspot.com/ to check live app.
9. To check backend api navigate to https://{{projectID}}.appspot.com/_ah/api/explorer.


Files:
- conference.py: All Python functions for API of app.
- models.py: Framework for all fields to be passed to datastore/used in API.
- app.yaml: API config/routing
- cron.yaml: right now functions every hour to update the announcement for app
- index.yaml: stores the queries in conference app
- main.py: contains background tasks for app
- settings.py: has web client to run app
- utils.py: fetches user ID

Session object:
- session_name: String property to store session name.
- highlights: String property to store session description.
- duration: Integer property to store duration of session.
- typeOfSession: String property to store session type.
- startDate: Date property to store session start date.
- startTime: Time property to store session start time.

Speaker object:
- speakerName: String property for speaker's name.
- speakerInfo: Text property to give short bio on speaker.
- speakerContact: String property to store either phone or email (or both) for speaker.

For Task 1 I created two new objects, for session information and speaker information. Using the conference keys, I linked each session to its respective conference. Speaker is linked by session. This allows for easy querying between conferences, sessions and speakers.

Task 2 was adding the wishlist. addSessionToWishlist adds sessions to user's wishlist. deleteSessionFromWishlist deletes session from user's wishlist. getSessionsInWishlist queries all sessions in user's wishlist.

Task 3 needed additional queries. getSpeakersByConf gets speaker information for a given conference by querying all unique speaker keys related to each session in a given conference. 
getSessionByTime gets the sessions at a given time.
Task 3's issue was that the datastore queries cannot accept a query with two not equal statements. This was resolved by querying the sessions twice. The first query checks for all sessions that do not have a "workshop" type of session. The second query goes through the != workshop results and returns all results from the first query that are before 7PM.

For Task 4, I set a SetSpeaker task that updates the memcache with the speaker who has the most sessions in a given conference.

Please check the comments in conference.py for specific details on functionality.