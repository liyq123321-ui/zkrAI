import json
import uuid

from app.database.database import SessionLocal
from app.database.models import Epic, WorkItem



def save_plan(
    session_id,
    output
):

    data=json.loads(output)


    db=SessionLocal()


    epic_id=str(uuid.uuid4())


    epic=Epic(

        id=epic_id,

        session_id=session_id,

        title=data["epic"]["title"],

        description=data["epic"].get(
            "description",
            ""
        )

    )


    db.add(epic)



    for item in data["departments"]:


        work=WorkItem(

            id=str(uuid.uuid4()),

            epic_id=epic_id,

            department=item["department"],

            title=item["title"],

            spec=item["spec"],

            assigned_agent=item["agent"]

        )


        db.add(work)


    db.commit()

    db.close()


    return epic_id
