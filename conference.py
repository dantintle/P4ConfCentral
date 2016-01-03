#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionFormByConference
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_SPEAKER_KEY = "SET_SPEAKER"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_CREATE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
    speakerKey = messages.StringField(2),
    session_name = messages.StringField(3),
    duration = messages.StringField(4),
    typeOfSession = messages.StringField(5),
    startDate = messages.StringField(6),
    startTime = messages.StringField(7)
    )

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
    typeOfSession = messages.StringField(2)
    )

SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speakerKey = messages.StringField(1)
    )

SESSION_GET_REQUEST = endpoints.ResourceContainer(message_types.VoidMessage,
    sessionKey = messages.StringField(1)
    )

SPEAKER_POST_REQUEST = endpoints.ResourceContainer(message_types.VoidMessage, 
    speakerName=messages.StringField(1),
    speakerInfo=messages.StringField(2), 
    speakerContact=messages.StringField(3)
    )

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', 
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # TODO 2: add confirmation email sending task to queue

        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        """Update Conference Object, return _copyConferenceToForm."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
                # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id
                # Look for TODO 2
        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id =  getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in conferences]
        )


#----speaker

    def _copySpeakerToForm(self, speaker):
        """Check for speaker info, return speakerForm with speaker information."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf

    @endpoints.method(SPEAKER_POST_REQUEST, BooleanMessage, path='speakers/add', http_method='POST', name='addSpeaker')
    def addSpeaker(self, request):
        """Get profile from user, take field info for all fields, give key and put into datastore"""
        prof=self._getProfileFromUser()
        data={field.name:getattr(request, field.name) for field in request.all_fields()}
        s_id = Speaker.allocate_ids(size=1)[0]
        speaker_key = ndb.Key(Speaker, s_id)
        data['key'] = speaker_key
        Speaker(**data).put()
        return BooleanMessage(data=True)

    @endpoints.method(message_types.VoidMessage, SpeakerForms, path = 'speakers/get', http_method = 'POST', name = 'getSpeakers')
    def getSpeakers(self, request):
        """Query datastore for all speakers."""
        speakers = Speaker.query()
        return SpeakerForms(
            items = [self._copySpeakerToForm(speaker)
            for speaker in speakers]
            )

    @endpoints.method(CONF_GET_REQUEST, SpeakerForms, path='speakers/getSpeakersByConf/{websafeConferenceKey}', http_method='POST', name='getSpeakersByConf')
    def getSpeakersByConf(self, request):
        """Populate all speakers for a given conference key."""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor = confKey, projection = ["speakerKey"], distinct = True)
        speakerKeys = [(ndb.Key(urlsafe = sess.speakerKey)) for sess in sessions]
        confSpeakers = ndb.get_multi(speakerKeys)
        return SpeakerForms(
            items = [self._copySpeakerToForm(confSpeaker)
            for confSpeaker in confSpeakers
            ]
            )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

#-----session

    def _copySessionToForm(self, sess, conferenceName, speakerName):
        """Returns session form given user input."""
        session = SessionForm()
        for field in session.all_fields():
            if hasattr(sess, field.name):
                if field.name.endswith('Date'):
                    setattr(session, field.name,str(getattr(sess, field.name)))
                elif field.name.endswith('Time'):
                    setattr(session, field.name, str(getattr(sess, field.name)))
                else:
                    setattr(session, field.name, str(getattr(sess, field.name)))
            elif field.name == "websafeSessionKey":
                setattr(session, field.name, sess.key.urlsafe())
        if conferenceName:
            setattr(session, 'conferenceName', conferenceName)
        if speakerName:
            setattr(session, 'speakerName', speakerName)
        session.check_initialized()
        return session

    @endpoints.method(SESSION_CREATE, SessionForm, path='session', http_method='POST', name='createSession')
    def createSession(self, request):
        """Create session."""
        #check for conference key/confirm user is person who created conference.
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException("Need authorization")
        user_id = getUserId(user)
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = confKey.get()
        if not conf:
            raise endpoints.BadRequestException("No conference found.")

        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException("Only owner can add sessions.")
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        del data['websafeConferenceKey']

        #check data/start time/duration fields, get values

        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], "%H:%M").time()
        if data['duration']:
            data['duration'] = int(data['duration'])
        else:
            data['duration'] = 0
            #give session key with conference key as parent. allows speaker to be set via speaker key
        s_id = Session.allocate_ids(size = 1, parent=confKey)[0]
        s_key = ndb.Key(Session, s_id, parent=confKey)
        data['key'] = s_key
        session = Session(**data)
        session.put()
        taskqueue.add(params={'speakerKey': request.speakerKey, 'conferenceKey': request.websafeConferenceKey}, url='/tasks/set_speaker')

        return self._copySessionToForm(session, "", "")

    @endpoints.method(CONF_GET_REQUEST, SessionForms, path='getConferenceSessions/{websafeConferenceKey}', http_method = 'POST', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Query datastore for all sessions based on conference key."""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=confKey)
        conferenceName = confKey.get().name
        return SessionForms(
            items=[self._copySessionToForm(sess, conferenceName, ndb.Key(urlsafe=sess.speakerKey).get().speakerName) for sess in sessions])

    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms, path='getConferenceSessionsByType/{websafeConferenceKey}/{typeOfSession}', http_method='POST', name='getConferenceSessionByType')
    def getConferenceSessionByType(self, request):
        """Given conference key, query sessions with filter for session type."""
        confKey = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=confKey).filter(Session.typeOfSession == request.typeOfSession)
        conferenceName = confKey.get().name
        return SessionForms(items = [self._copySessionToForm(sess, conferenceName, ndb.Key(urlsafe = sess.speakerKey).get().speakerName) for sess in sessions]
            )

    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms, path='getSessionsBySpeaker/{speakerKey}/', http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Query all sessions which speaker is in, given the speaker key."""
        wssk = request.speakerKey
        sessions = Session.query().filter(Session.speakerKey == wssk).get()
        speaker = ndb.Key(urlsafe=wssk).get()
        return SessionForms(
            items = [self._copySessionToForm(sess, sess.key.parent().get().name, speaker.speakerName) for sess in sessions]
            )

    @endpoints.method(message_types.VoidMessage, SessionForms, path='getWorkshopSessionBeforeSeven', http_method='GET', name='getWorkshopSessionBeforeSeven')
    def getWorkShopSessionBeforeSeven(self, request):
        """Query sessions for all not workshop, before 7 PM."""
        #query session by type not workshop
        sessions = Session.query(Session.typeOfSession!= "workshop").fetch()
        validSessions = []
        #for all sessions in query, check time to see if it's before 19:00 (7PM)
        for sess in sessions:
            if sess.startTime < datetime.strptime("19:00", "%H:%M").time():
                validSessions.append(sess)
        #return all sessions fitting criteria
        return SessionForms(
            items=[self._copySessionToForm(sess, sess.key.parent().get().name, ndb.Key(urlsafe=sess.speakerKey).get().speaker_name) for sess in validSessions]
            )

# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

#---wishlist
    def _wishlistManager(self, request, add=True):
        """Add/remove sessions from wishlist. Return BooleanMessage."""
        #set boolvar to false (this is our variable to confirm success of functions). get user profile from login, sessionKey of session to add to wishlist
        boolvar = False
        prof = self._getProfileFromUser()
        sessionKey = request.sessionKey
        session = ndb.Key(urlsafe=sessionKey).get()

        #if wishlist add and sessionkey doesn't exist, say no session found. if session key already in profile wishlist, say already exist. if new return boolvar true.
        if add:
            if not session:
                raise endpoints.NotFoundException(
                    'No session found.')
            if sessionKey in prof.sessionWishlist:
                raise ConflictException("Session already in wishlist.")
            prof.sessionWishlist.append(sessionKey)
            boolvar = True

        else:
            #if deleting and in profile, remove session key for session, return true.
            if sessionKey in prof.sessionWishlist:
                prof.sessionWishlist.remove(sessionKey)
                boolvar = True
            else:
                #if session does not exist, say session cannot be deleted as it is not in wishlist.
                raise ConflictException(
                    "Session not in wishlist.")
        prof.put()
        return BooleanMessage(data=boolvar)

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,path='addSessionToWishlist', http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Return wishlist manager with request."""
        return self._wishlistManager(request)

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage, path='deleteSessionFromWishlist', http_method='POST', name='deleteSessionFromWishlist')
    def deleteSessionFromWishlist(self, request):
        """Return to manager with delete request."""
        return self._wishlistManager(request, add=False)

    @endpoints.method(message_types.VoidMessage, SessionForms, path='getSessionsInWishlist', http_method='POST', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """List all sessions for user profile."""
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=sessionKey) for sessionKey in prof.sessionWishlist]
        sessions = ndb.get_multi(session_keys)
        return SessionForms(
            items = [self._copySessionToForm(sess, sess.key.parent().get().name, ndb.Key(urlsafe=sess.speakerKey).get().speakerName) for sess in sessions]
            )

# - - - Announcements - - - - - - - - - - - - - - - - - - - -

# static methods for cache

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @staticmethod
    def _cacheSpeaker(speakerKey, conferenceKey):
        """Store main speaker in memcache."""
        #set speaker key and conference key. Query sessions with conference key. If sessions > 1, get speaker key that fits that criteria and set as speaker. 
        speakerKey = speakerKey
        confKey = ndb.Key(urlsafe=conferenceKey)
        query = Session.query(ancestor=confKey)
        sessions = query.filter(Session.speakerKey == speakerKey).count()
        cacheSpeaker = ""
        if sessions > 1:
            sKey = ndb.Key(urlsafe=speakerKey)
            speaker = sKey.get()
            speakerMsg = '%s %s' % (
                'The main speaker for this conference is: ', speaker.speakerName)
            memcache.set(MEMCACHE_SPEAKER_KEY, speakerMsg)
        return speakerMsg

    @endpoints.method(message_types.VoidMessage, StringMessage, path='conference/speaker/get', http_method='GET', name='getSpeaker')
    def getSpeaker(self, request):
        """Get speaker from memcache."""
        cacheSpeaker = memcache.get(MEMCACHE_SPEAKER_KEY)
        if not cacheSpeaker:
            cacheSpeaker = ""
        return StringMessage(data=Speaker)

# TODO 1

api = endpoints.api_server([ConferenceApi]) # register API
