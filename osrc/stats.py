#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (division, print_function, absolute_import,
                        unicode_literals)

__all__ = ["get_user_info"]

import flask
import requests

from .index import get_neighbors
from .timezone import estimate_timezone
from .database import get_pipeline, format_key

ghapi_url = "https://api.github.com/users/{username}"


def get_user_info(username):
    # Normalize the username.
    user = username.lower()

    # Get the cached information.
    pipe = get_pipeline()
    pipe.get(format_key("user:{0}:name".format(user)))
    pipe.get(format_key("user:{0}:etag".format(user)))
    pipe.get(format_key("user:{0}:gravatar".format(user)))
    pipe.get(format_key("user:{0}:tz".format(user)))
    name, etag, gravatar, timezone = pipe.execute()
    if name is not None:
        name = name.decode("utf-8")

    # Work out the authentication headers.
    auth = {}
    client_id = flask.current_app.config.get("GITHUB_ID", None)
    client_secret = flask.current_app.config.get("GITHUB_SECRET", None)
    if client_id is not None and client_secret is not None:
        auth["client_id"] = client_id
        auth["client_secret"] = client_secret

    # Perform a conditional fetch on the database.
    headers = {}
    if etag is not None:
        headers = {"If-None-Match": etag}

    r = requests.get(ghapi_url.format(username=username), params=auth,
                     headers=headers)
    code = r.status_code
    if code != 304 and code == requests.codes.ok:
        data = r.json()
        name = data.get("name") or data.get("login") or username
        etag = r.headers["ETag"]
        gravatar = data.get("gravatar_id", "none")
        location = data.get("location", None)
        if location is not None:
            tz = estimate_timezone(location)
            if tz is not None:
                timezone = tz

        # Update the cache.
        pipe.set(format_key("user:{0}:name".format(user)), name)
        pipe.set(format_key("user:{0}:etag".format(user)), etag)
        pipe.set(format_key("user:{0}:gravatar".format(user)), gravatar)
        if timezone is not None:
            pipe.set(format_key("user:{0}:tz".format(user)), timezone)
        pipe.execute()

    # Get the nearest neighbors in behavior space.
    similar_users = get_neighbors(user)

    return {
        "name": name if name is not None else username,
        "gravatar": gravatar if gravatar is not None else "none",
        "timezone": int(timezone) if timezone is not None else None,
        "similar_users": similar_users,
    }


def make_histogram(data, size, offset=0):
    result = [0] * size
    for k, v in data:
        val = float(v)
        i = int(k) + offset
        while (i < 0):
            i += size
        result[i % size] = val
    return result


def get_usage_stats(username):
    user = username.lower()
    pipe = get_pipeline()

    # Get the total number of events performed by this user.
    pipe.zscore(format_key("user"), user)

    # The timezone estimate.
    pipe.get(format_key("user:{0}:tz".format(user)))

    # Get the top <= 5 most common events.
    pipe.zrevrangebyscore(format_key("user:{0}:event".format(user)),
                          "+inf", 0, 0, 5, withscores=True)

    # The average daily and weekly schedules.
    pipe.hgetall(format_key("user:{0}:hour".format(user)))
    pipe.hgetall(format_key("user:{0}:day".format(user)))

    # The language stats.
    pipe.zrevrange(format_key("user:{0}:lang".format(user)), 0, -1,
                   withscores=True)

    # Parse the results.
    results = pipe.execute()
    total_events = int(results[0]) if results[0] is not None else 0
    if not total_events:
        return None
    timezone = results[1]
    offset = int(timezone) + 8 if timezone is not None else 0
    event_counts = results[2]
    daily_histogram = make_histogram(results[3].items(), 24, offset)
    weekly_histogram = make_histogram(results[4].items(), 7)
    languages = results[5]

    # Parse the languages into a nicer form.
    languages = [{"language": l, "count": int(c)} for l, c in languages]

    return {
        "total_events": total_events,
        "event_counts": event_counts,
        "daily_histogram": map(int, daily_histogram),
        "weekly_histogram": map(int, weekly_histogram),
        "languages": languages,
    }