# ADR 0001: Layered architecture

Status: accepted

Application behavior is expressed as tools over domain protocols. Infrastructure
implements the protocols and interfaces render results. Dependencies point
inward so delivery mechanisms cannot own retrieval behavior.
