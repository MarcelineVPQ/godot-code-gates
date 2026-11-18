extends Node


func _physics_process(_delta: float) -> void:
	step_one()


func step_one() -> void:
	step_two()


func step_two() -> void:
	step_three()


func step_three() -> void:
	var i := 0
	while i < 10:
		i += 1
