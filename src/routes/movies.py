import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from database import MovieModel, get_db
from database.models import ActorModel, CountryModel, GenreModel, LanguageModel
from schemas.movies import (
    MovieCreateSchema,
    MovieDetailSchema,
    MovieListItemSchema,
    MovieListResponseSchema,
    MovieUpdateSchema,
)

router = APIRouter()

MOVIE_NOT_FOUND = "Movie with the given ID was not found."


def _with_relations(stmt):
    return stmt.options(
        joinedload(MovieModel.country),
        selectinload(MovieModel.genres),
        selectinload(MovieModel.actors),
        selectinload(MovieModel.languages),
    )


async def _get_movie_with_relations(db: AsyncSession, movie_id: int):
    stmt = _with_relations(select(MovieModel).where(MovieModel.id == movie_id))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_or_create_by_name(
        db: AsyncSession, model, names: list[str]
) -> list:
    unique_names = list(dict.fromkeys(names))
    instances = []
    for name in unique_names:
        result = await db.execute(select(model).where(model.name == name))
        instance = result.scalars().first()
        if instance is None:
            instance = model(name=name)
            db.add(instance)
            await db.flush()
        instances.append(instance)
    return instances


async def _get_or_create_country(
        db: AsyncSession, code: str
) -> CountryModel:
    result = await db.execute(
        select(CountryModel).where(CountryModel.code == code)
    )
    country = result.scalars().first()
    if country is None:
        country = CountryModel(code=code)
        db.add(country)
        await db.flush()
    return country


@router.get("/movies/", response_model=MovieListResponseSchema)
async def get_movies(
        page: int = Query(1, ge=1),
        per_page: int = Query(10, ge=1, le=20),
        db: AsyncSession = Depends(get_db),
):
    total_items = (
        await db.execute(select(func.count(MovieModel.id)))
    ).scalar_one()

    if total_items == 0:
        raise HTTPException(status_code=404, detail="No movies found.")

    total_pages = math.ceil(total_items / per_page)
    if page > total_pages:
        raise HTTPException(status_code=404, detail="No movies found.")

    offset = (page - 1) * per_page
    stmt = (
        select(MovieModel)
        .order_by(MovieModel.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    movies = (await db.execute(stmt)).scalars().all()

    if not movies:
        raise HTTPException(status_code=404, detail="No movies found.")

    prev_page = (
        f"/theater/movies/?page={page - 1}&per_page={per_page}"
        if page > 1
        else None
    )
    next_page = (
        f"/theater/movies/?page={page + 1}&per_page={per_page}"
        if page < total_pages
        else None
    )

    return MovieListResponseSchema(
        movies=[MovieListItemSchema.model_validate(m) for m in movies],
        prev_page=prev_page,
        next_page=next_page,
        total_pages=total_pages,
        total_items=total_items,
    )


@router.post(
    "/movies/",
    response_model=MovieDetailSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_movie(
        payload: dict,
        db: AsyncSession = Depends(get_db),
):
    try:
        movie_data = MovieCreateSchema.model_validate(payload)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid input data.")

    existing = await db.execute(
        select(MovieModel).where(
            MovieModel.name == movie_data.name,
            MovieModel.date == movie_data.date,
        )
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A movie with the name '{movie_data.name}' and "
                f"release date '{movie_data.date}' already exists."
            ),
        )

    try:
        country = await _get_or_create_country(db, movie_data.country)
        genres = await _get_or_create_by_name(
            db, GenreModel, movie_data.genres
        )
        actors = await _get_or_create_by_name(
            db, ActorModel, movie_data.actors
        )
        languages = await _get_or_create_by_name(
            db, LanguageModel, movie_data.languages
        )

        movie = MovieModel(
            name=movie_data.name,
            date=movie_data.date,
            score=movie_data.score,
            overview=movie_data.overview,
            status=movie_data.status,
            budget=movie_data.budget,
            revenue=movie_data.revenue,
            country=country,
            genres=genres,
            actors=actors,
            languages=languages,
        )
        db.add(movie)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    created = await _get_movie_with_relations(db, movie.id)
    return MovieDetailSchema.model_validate(created)


@router.get("/movies/{movie_id}/", response_model=MovieDetailSchema)
async def get_movie_by_id(movie_id: int, db: AsyncSession = Depends(get_db)):
    movie = await _get_movie_with_relations(db, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail=MOVIE_NOT_FOUND)
    return MovieDetailSchema.model_validate(movie)


@router.delete("/movies/{movie_id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_movie(movie_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MovieModel).where(MovieModel.id == movie_id)
    )
    movie = result.scalars().first()
    if movie is None:
        raise HTTPException(status_code=404, detail=MOVIE_NOT_FOUND)

    await db.delete(movie)
    await db.commit()


@router.patch("/movies/{movie_id}/")
async def update_movie(
        movie_id: int,
        payload: dict,
        db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MovieModel).where(MovieModel.id == movie_id)
    )
    movie = result.scalars().first()
    if movie is None:
        raise HTTPException(status_code=404, detail=MOVIE_NOT_FOUND)

    try:
        movie_data = MovieUpdateSchema.model_validate(payload)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid input data.")

    for field, value in movie_data.model_dump(exclude_unset=True).items():
        setattr(movie, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    return {"detail": "Movie updated successfully."}
