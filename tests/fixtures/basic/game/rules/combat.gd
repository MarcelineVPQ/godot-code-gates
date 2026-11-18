extends RefCounted


static func resolve(units: Array) -> int:
	var total := 0
	for u in units:
		total += u.hp
	for u in units:
		total += sqrt(u.armor)
	return total
