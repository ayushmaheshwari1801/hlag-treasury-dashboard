"""Read confirmed numeric Major Changes without legacy projections."""

def major_observations(result):
    return [dict(change.numeric.model_dump(mode='json'), change_id=change.change_id,
                 direction=change.direction)
            for change in result.changes
            if change.verification == 'confirmed' and change.numeric is not None]
